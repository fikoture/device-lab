# Local LLM research for a GTX 1060 6 GB (November 2026)

Hardware: Surface Book 2, NVIDIA GeForce GTX 1060 Mobile 6 GB (Pascal, compute capability 6.1, no Tensor Cores), driver 580 server, Ubuntu Server 26.04 LTS, Ollama.

## Hard constraints

| Topic | Finding | Source |
|---|---|---|
| Ollama support | "Ollama supports Nvidia GPUs with compute capability 5.0+ and driver version 550 and newer." Cards with compute capability 5.0 to 6.2 need driver 570 or newer. GTX 1060 is listed. | Ollama GPU docs |
| Driver branch | 580 is the last NVIDIA branch that supports Pascal. | bestllmfor.com GTX 1060 guide |
| CUDA | CUDA 13 dropped Pascal. Ollama uses its bundled CUDA 12 runner for older cards. Check with `ollama ps`: PROCESSOR must show 100% GPU. | bestllmfor.com GTX 1060 guide |
| vLLM | Not an option: "GPU: compute capability 7.0 or higher". Community Pascal builds are archived or unverified, and AWQ output is reported broken on Pascal. | vLLM docs, vllm-ci, vLLM PR 5058 |
| Memory | Download size is not total VRAM. The KV cache adds memory on top. On 6 GB, about 3 to 4 GB models run comfortably; 4.7 GB and up only with short context. | Morph, RunAIHome |

## Candidates

| Model | Ollama tag | Download | Fits 6 GB | Notes |
|---|---|---|---|---|
| Qwen 3.5 4B | `qwen3.5:4b` | 3.3 to 4.0 GB | Yes | 2026 all-rounder; tools, thinking, image input, 256K context; broad multilingual support |
| NVIDIA Nemotron 3 Nano 4B | `nemotron-3-nano:4b` | 2.8 to 2.9 GB | Yes | NVIDIA, March 2026; hybrid Mamba-Transformer; NVIDIA reports the lowest VRAM use in its class and strong instruction following and tool use; 18 tokens/s on Jetson Orin Nano. Languages listed: English, German, Spanish, French, Italian, Japanese (no Turkish). |
| IBM Granite 4.2 3B | `granite4.2:3b` | 2.2 GB | Yes | Tool calling and JSON output; Apache 2.0 |
| Llama 3.2 3B | `llama3.2:3b` | 2.0 GB | Yes | Speed baseline: 50.41 tokens/s measured on an RTX 2060 |
| Phi-4-mini 3.8B | `phi4-mini` | about 2.5 to 3 GB | Yes | Predictable, structured answers; 15 to 30 tokens/s expected on 6 GB cards |
| Qwen 2.5 Coder 7B | `qwen2.5-coder:7b` | 4.7 GB | Tight | Code; short context only |
| LiquidAI LFM2.5 8B-A1B | `lfm2.5:8b` | 5.2 GB | Tight | Mixture of experts, about 1B active parameters per token; benchlm score 35.6 |
| Gemma 4 E2B | `gemma4:e2b` | 4.6 GB | Tight | Vision and audio parts make it large for its class |
| Gemma 4 E4B, Qwen 3.5 9B | `gemma4:e4b`, `qwen3.5:9b` | 6.6 GB+ | No | Spill into system memory and slow down |

## Test plan

Same three tasks for every model:
1. Turkish explanation: "API testi nedir, 3 cumleyle anlat."
2. Log analysis: real portal log lines (401, 404), "which errors, what is the likely cause?"
3. Code: a small Python function.

Measured per run: eval rate (tokens/s) from `ollama run <model> --verbose`, GPU placement from `ollama ps`, VRAM from `nvidia-smi`, and answer quality scored by hand.

Results: to be filled in after the benchmark.

## Sources

- Ollama GPU docs: https://docs.ollama.com/gpu
- Ollama library: https://ollama.com/library/qwen3.5, https://ollama.com/library/gemma4, https://ollama.com/library/nemotron-3-nano
- NVIDIA, Nemotron 3 Nano 4B: https://huggingface.co/blog/nvidia/nemotron-3-nano-4b
- Morph, Best Ollama Models (updated Nov 2026): https://www.morphllm.com/best-ollama-models
- BenchLM, Best Ollama Models (Nov 2026): https://benchlm.ai/best/ollama-models
- RunAIHome, Best Local LLMs for 6GB VRAM (Aug 2026): https://runaihome.com/blog/best-local-llm-6gb-vram-2026/
- GTX 1060 guide: https://bestllmfor.com/guide/llm-gtx-1060/
- vLLM GPU requirements: https://docs.vllm.ai/en/v0.10.2/getting_started/installation/gpu.html
