# Kill Test 执行方案：动作清单 × 判定标准

> **状态**:待执行 · 配套 `evaluation_protocol_v2.md`(协议)、`v1_implementation_guide.md`(实施)、`memory_three_layer_design.md`(设计)、`positioning_and_related_work.md`(定位)。
>
> **一句话**:同一批 (ticker, date)，两臂(LESSONS 开/关)各跑 3 轮，比方向命中率，用置信区间判定"提升 / 非劣效 / 劣化"。120 runs 后填判定树,决定论文主线与投稿目标。

---

## 0. 前置工程动作(不干净就别开跑)

### 0.1 加干净的开关

- 新增配置 `TRADINGAGENTS_LESSONS_ENABLED`(bool):
  - `off`(基线臂):`_build_past_context` 退化为 `get_past_context`(决策日志 n_same=5 / n_cross=3 平权注入);
  - `on`(处理臂):三段合并(L3 准则 + L1 事件 top-k + 决策日志兜底)。
- **铁律:两臂必须只差"记忆注入内容",其余(模型档位、分析师编制、数据源、温度、种子、注入条数上限)完全一致。**
- 验收:写一个断言测试,列出两臂全部可配置项,断言除 lessons 外无差异。

### 0.2 写死映射规则(先定规则,再跑数据)

| 项 | 规则(示例,按仓库实际落盘格式确认后定稿) |
|---|---|
| rating → 方向 | 买类(strong buy/buy)= +1;卖类(strong sell/sell)= −1;hold = 0 |
| 命中定义 | `sign(评级方向) == sign(已实现 alpha)`,hold 不计入主指标,单独报告 |
| 已实现 alpha | `_fetch_returns` 结算输出(相对区域基准,非 raw return) |
| 主指标 | 方向命中率 p = 命中数 / 有效配对点(排除 hold 后) |

- 映射规则写进实验日志(`experiment_log.md`),作为审计记录。

---

## 1. 配对清单(20 个点)

- 来源:`demo_pool_ashare.json`(A 股池);
- 结构:`killtest_pairs.json` = `[{ "ticker": "...", "trade_date": "YYYY-MM-DD", "settle_days": 14 }] × 20`;
- **日期约束**:`trade_date` 距今 ≥ `settle_days` + 缓冲(如 14 + 7 = 21 天),保证收益已实现、yfinance 确定性可得;
- **预筛**:优先选历史信号以买卖为主(hold 占比低)的日期,避免有效样本缩水;
- 配对所有点两臂共用,顺序固定。

---

## 2. 冒烟测试(先 2 个点)

跑 2 个配对点 × 两臂各 1 轮,确认:

- [ ] 两臂都能正常产出 rating 并完成结算(alpha 可取);
- [ ] 处理臂 `trading_lessons.md` 出现 `[EVENT | ...]` 条目,且注入进 PM prompt(`verify_l1.py` 4/4 PASS);
- [ ] `cost_log.csv` 字段齐全(input/output tokens、cost、provider、model);
- [ ] 两臂除 lessons 外无配置差异(0.1 的断言通过)。

**不过冒烟,不进入正式批。**

---

## 3. 正式跑批(120 runs)

```
基线臂:  20 配对点 × 3 轮 = 60 runs   (TRADINGAGENTS_LESSONS_ENABLED=off)
处理臂:  20 配对点 × 3 轮 = 60 runs   (TRADINGAGENTS_LESSONS_ENABLED=on)

参考命令(按实际环境微调):
  python scripts/run_pool.py --pool killtest_pairs.json \
      --analysts market,social,fundamentals \
      --settle --settle-days 14 --log cost_log.csv
```

**配对纪律**:
- 两臂同一份 manifest、同一执行顺序;
- 每轮固定 provider / model / 温度 / 种子 / 数据版本;
- 跑批期间**不调任何参数**(τ/K/γ 等一律不动);
- 中途失败:记录失败点,补跑该配对点,不跳过、不替换。

---

## 4. 指标计算(一个脚本收口)

每 run 输出 `(rating, alpha)` → 命中判定 → 汇总:

1. 每配对点:两臂各 3 轮 → 命中率 p_A、p_B;
2. Δp = p_B − p_A;配对点间计算 σ_d(差值标准差);
3. 输出:
   - **90% CI**(非劣效判定用);
   - **95% CI**(提升/劣化判定用);
4. 同时输出:hold 占比、有效配对点数、两臂平均注入 token、复盘触发率。

> 统计脚本建议 ≤30 行(配对 t 检验 + CI),吃结算输出与 cost_log 即可。

---

## 5. 判定标准(写死,三个出口)

| 判定 | 条件 | 结论 | 下一步 |
|---|---|---|---|
| **提升** | Δp 的 95% CI 下界 > 0 | 通过(惊喜档) | 主线 C2"同等预算更准",追加消融 → 投 NLPCC(CCF-C) |
| **非劣效** | Δp 的 90% CI ⊂ (−0.05, +0.05) | **通过(主档)** | 主线 C1 有界性 + C2 持平即赢 → 投 NLPCC / 正规 EI |
| **劣化** | Δp 的 95% CI 上界 < −0.05 | 不通过 | 诊断 → 修复重测一次 → 仍劣化则收缩 C1+C3(纯系统叙事,投正规 EI) |

**δ 纪律**:δ = 0.05(绝对 5pp)实验前写死,禁止看结果后回调;若基线 p0 接近 0.5(随机水平),δ 收紧至 0.03。

### 并行成本门槛(两条主线都要过)

| 门槛 | 标准 | 超限处理 |
|---|---|---|
| 注入预算 | 处理臂注入 token ≤ 基线 × 1.5 | L3 准则截断 top-10 / 压缩单条长度 |
| 复盘触发率 | 结算中 |alpha| ≥ 阈值 占比 ≤ 30% | 上调 `event_alpha_threshold` / 换 quick 档 |
| 写入侧摊薄 | 复盘 agent 单次成本 × 触发率 如实报告 | 不隐瞒,写进论文"成本"节 |

---

## 6. 三个最容易翻车的点

1. **开关不干净**:两臂差异 > lessons 本身 → 结论全是噪音;用 0.1 的断言防;
2. **配对日期选错**:`trade_date` 太新 → 收益未实现、alpha 取不出 → 整批作废;统一校验"已过 settle + 数据可拉";
3. **hold 占比过高**:样本里 hold ≥ 40% → 有效配对点大幅缩水;manifest 预筛优先买卖信号强的历史日期。

---

## 7. 输出物清单(跑完应有)

- [ ] `killtest_pairs.json`(20 点 manifest)
- [ ] `experiment_log.md`(映射规则、δ、配置快照、日期)
- [ ] 两臂完整 `cost_log.csv`(120+ runs 记录)
- [ ] 统计输出:Δp、90% CI、95% CI、σ_d、hold 占比、注入 token 对比
- [ ] 判定结论(三选一)+ 成本门槛核验
- [ ] 更新 `evaluation_protocol_v2.md` §4 执行状态与 `competition_implementation_roadmap.md` 进度

---

## 8. 与主线的关系(为什么这个顺序)

- kill test **只验证 C2(同等预算注入质量)**;C1(有界性)与 C3(遗忘保护)不依赖它,可并行用构造数据/基准测试先做;
- 判定为"非劣效"即达到投稿最低门槛——论文主线 C1+C2,不赌"记忆提升 alpha"(ATLAS 反证已被绕开);
- 判定为"提升"是惊喜,决定是否追加 v2(L3 蒸馏)/ v3(L2 检索)投入——它们只增强 C2,不增强 C1/C3。
