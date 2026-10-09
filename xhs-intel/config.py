"""Versioned, data-driven policy for XHS intelligence lanes.

The edge owns this small policy snapshot.  The local worker receives the
snapshot with a job, so changing a topic later cannot silently rewrite an old
decision.
"""
from __future__ import annotations

import hashlib
import json


DEFAULT_TOPICS = (
    {
        "slug": "ai_infra",
        "name": "AI 基础设施",
        "description": "GPU、集群、数据中心、算力、电力、散热、存储、网络和光互联",
        "include_keywords": ["GPU", "ASIC", "TPU", "数据中心", "AIDC", "datacenter", "集群", "cluster", "算力", "智算",
                             "电力", "散热", "液冷", "存储", "网络", "光模块", "CPO", "HBM", "NVLink", "InfiniBand",
                             "RoCE", "RDMA", "infra", "infrastructure"],
        "exclude_keywords": ["美妆", "穿搭", "旅游", "情感", "美食", "泛消费"],
        "search_keywords": ["AI 基础设施", "AI infra", "GPU 集群", "智算中心", "数据中心 算力", "NVLink", "光模块 CPO"],
        "threshold": 0.65,
    },
    {
        "slug": "training_systems",
        "name": "训练系统",
        "description": "分布式训练、并行策略、通信、容错、checkpoint 与后训练",
        "include_keywords": ["分布式训练", "distributed training", "训练", "training", "并行", "parallel", "parallelism",
                             "数据并行", "张量并行", "流水线并行", "专家并行", "通信", "NCCL", "容错", "checkpoint",
                             "ZeRO", "Megatron", "FSDP", "RLHF", "post-training", "强化学习训练"],
        "exclude_keywords": [],
        "search_keywords": ["分布式训练", "distributed training", "大模型训练", "并行训练", "NCCL", "Megatron"],
        "threshold": 0.65,
    },
    {
        "slug": "inference",
        "name": "推理系统",
        "description": "Serving、KV Cache、批处理、量化、投机解码和推理引擎",
        "include_keywords": ["推理", "inference", "serving", "部署", "deployment", "KV Cache", "量化", "quantization",
                             "投机解码", "speculative decoding", "SGLang", "vLLM", "TensorRT", "TensorRT-LLM",
                             "batching", "PD 分离", "prefill", "decode"],
        "exclude_keywords": [],
        "search_keywords": ["大模型推理", "LLM inference", "vLLM", "SGLang", "推理加速", "模型部署", "KV Cache"],
        "threshold": 0.65,
    },
    {
        "slug": "compiler_runtime",
        "name": "Kernel 与编译器",
        "description": "CUDA、kernel、Triton、CUTLASS、编译器、Runtime 和图优化",
        "include_keywords": ["CUDA", "kernel", "算子", "Triton", "CUTLASS", "CuTe", "PTX", "编译器", "compiler",
                             "Runtime", "图优化", "torch.compile", "TVM", "flash attention", "GPU 编程", "汇编优化"],
        "exclude_keywords": [],
        "search_keywords": ["CUDA", "CUDA kernel", "算子优化", "Triton", "GPU 编程", "CUTLASS"],
        "threshold": 0.65,
    },
    {
        "slug": "models",
        "name": "模型与算法",
        "description": "LLM、VLM、MoE、多模态、Agent、算法与评测",
        "include_keywords": ["大模型", "LLM", "VLM", "MoE", "Transformer", "diffusion", "World Model", "Agent", "智能体",
                             "模型", "model", "算法", "algorithm", "预训练", "pretrain", "微调", "fine-tune", "RAG",
                             "多模态", "multimodal", "评测", "benchmark", "DeepSeek", "Qwen", "Llama"],
        "exclude_keywords": [],
        "search_keywords": ["大模型", "LLM", "Agent 智能体", "MoE", "多模态模型", "DeepSeek", "开源模型"],
        "threshold": 0.65,
    },
    {
        "slug": "research",
        "name": "AI 科研",
        "description": "论文、Benchmark、实验室、会议和复现",
        "include_keywords": ["论文", "paper", "Benchmark", "arXiv", "NeurIPS", "ICLR", "ICML", "OSDI", "SOSP", "MLSys",
                             "ISCA", "ASPLOS", "实验室", "复现", "reproduce", "科研", "research", "顶会"],
        "exclude_keywords": ["纯招聘", "求职广告"],
        "search_keywords": ["AI 论文", "arXiv 论文解读", "顶会论文", "paper reading"],
        "threshold": 0.65,
    },
    {
        "slug": "systems",
        "name": "系统工程",
        "description": "操作系统、容器、Kubernetes、集群调度和可观测性",
        "include_keywords": ["操作系统", "容器", "container", "Kubernetes", "K8s", "调度", "scheduling", "scheduler",
                             "Slurm", "Ray", "可观测性", "observability", "Linux", "云原生", "cloud native",
                             "分布式系统", "distributed system", "系统软件"],
        "exclude_keywords": [],
        "search_keywords": ["Kubernetes", "集群调度", "云原生", "可观测性", "分布式系统"],
        "threshold": 0.65,
    },
    {
        "slug": "finance",
        "name": "财经与量化",
        "description": "量化交易、股票、基金、宏观与市场研究",
        "include_keywords": ["量化", "quant", "量化交易", "量化策略", "财经", "股票", "股市", "A股", "港股", "美股",
                             "基金", "ETF", "期货", "期权", "债券", "宏观", "财报", "回测", "backtest", "因子",
                             "alpha", "交易策略", "投资", "行情"],
        "exclude_keywords": ["荐股广告", "开户引流", "课程推销", "付费社群"],
        "search_keywords": ["量化交易", "量化策略", "财经", "股票 复盘", "A股", "美股", "宏观经济", "因子模型"],
        "threshold": 0.65,
    },
)


def policy_snapshot(topics=None, version: int = 1) -> dict:
    rows = list(topics or DEFAULT_TOPICS)
    return {"version": int(version), "topics": rows, "negative_policy": ["美妆", "穿搭", "旅游", "情感", "美食", "泛消费"]}


def policy_hash(policy: dict) -> str:
    encoded = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def topic_slugs(policy: dict | None = None) -> set[str]:
    return {str(row.get("slug")) for row in (policy or policy_snapshot()).get("topics", []) if row.get("slug")}
