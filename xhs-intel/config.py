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
        "description": "GPU、数据中心、电力、散热、存储、网络和光互联",
        "include_keywords": ["GPU", "ASIC", "数据中心", "AIDC", "电力", "散热", "存储", "网络", "光模块", "CPO", "NVLink", "InfiniBand", "RoCE"],
        "exclude_keywords": ["美妆", "穿搭", "旅游", "情感", "美食", "泛消费"],
        "search_keywords": ["AI 基础设施", "GPU 数据中心", "光模块 CPO"],
        "threshold": 0.65,
    },
    {
        "slug": "training_systems",
        "name": "训练系统",
        "description": "分布式训练、并行、通信、容错和检查点",
        "include_keywords": ["分布式训练", "数据并行", "张量并行", "流水线并行", "通信", "容错", "checkpoint", "ZeRO", "NCCL"],
        "exclude_keywords": [],
        "search_keywords": ["分布式训练", "大模型训练"],
        "threshold": 0.65,
    },
    {
        "slug": "inference",
        "name": "推理系统",
        "description": "Serving、KV Cache、批处理、量化和推理引擎",
        "include_keywords": ["推理", "inference", "serving", "KV Cache", "量化", "投机解码", "SGLang", "vLLM", "TensorRT", "batching"],
        "exclude_keywords": [],
        "search_keywords": ["大模型推理", "vLLM", "SGLang"],
        "threshold": 0.65,
    },
    {
        "slug": "compiler_runtime",
        "name": "编译器与运行时",
        "description": "CUDA、Triton、编译器、Kernel、Runtime 和图优化",
        "include_keywords": ["CUDA", "Triton", "编译器", "Kernel", "算子", "Runtime", "图优化", "Tensor"],
        "exclude_keywords": [],
        "search_keywords": ["CUDA 优化", "算子优化", "Triton"],
        "threshold": 0.65,
    },
    {
        "slug": "models",
        "name": "模型与 Agent",
        "description": "LLM、VLM、MoE、Transformer、World Model 和 Agent",
        "include_keywords": ["大模型", "LLM", "VLM", "MoE", "Transformer", "World Model", "Agent", "模型", "预训练", "评测"],
        "exclude_keywords": [],
        "search_keywords": ["大模型", "Agent 智能体", "MoE"],
        "threshold": 0.65,
    },
    {
        "slug": "research",
        "name": "AI 科研",
        "description": "论文、Benchmark、实验室、会议和复现",
        "include_keywords": ["论文", "paper", "Benchmark", "arXiv", "NeurIPS", "OSDI", "SOSP", "实验室", "复现", "科研"],
        "exclude_keywords": ["纯招聘", "求职广告"],
        "search_keywords": ["AI 论文", "arXiv 论文解读"],
        "threshold": 0.65,
    },
    {
        "slug": "systems",
        "name": "系统工程",
        "description": "操作系统、容器、Kubernetes、调度和可观测性",
        "include_keywords": ["操作系统", "容器", "Kubernetes", "K8s", "调度", "可观测性", "Linux", "系统软件"],
        "exclude_keywords": [],
        "search_keywords": ["Kubernetes", "可观测性"],
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
