from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# 复制默认配置
config = DEFAULT_CONFIG.copy()

# 修改为使用本地Ollama模型
config["llm_provider"] = "ollama"
config["deep_think_llm"] = "deepseek-r1:8b"    # 复杂推理用这个
config["quick_think_llm"] = "qwen2.5:14b"     # 简单任务用这个
config["max_debate_rounds"] = 1               # 调试时先用1轮，减少等待时间

print("正在初始化TradingAgents...")
ta = TradingAgentsGraph(debug=True, config=config)

print("开始分析AAPL股票...")
result, decision = ta.propagate("AAPL", "2026-01-15")

print("=" * 50)
print("最终决策：", decision)
print("=" * 50)