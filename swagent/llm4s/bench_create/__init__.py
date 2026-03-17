"""
bench_create: Task2 趋势预测评测数据集构建模块

独立于现有 Task2 workflow，负责：
1. 从 KG JSONL 数据中按领域/关键词/年份进行全量扫描统计
2. 为50个固废子领域构建结构化 benchmark 数据
3. 输出 JSONL 格式的评测数据集
"""
