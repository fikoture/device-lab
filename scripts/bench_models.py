#!/usr/bin/env python3
"""bench_models.py: measure local Ollama models on the same tasks.

For every model and every task it records:
  - generation speed (tokens per second, from Ollama's own eval_count / eval_duration)
  - load time (first call loads the model into GPU memory)
  - where the model runs (ollama ps: "100% GPU" is what we want)
  - GPU memory used (nvidia-smi)
and saves every answer to results/<model>/<task>.txt so quality can be scored by a human.

Usage:  python3 bench_models.py                 (default model list)
        python3 bench_models.py llama3.2:3b     (only the models you name)
Only the Python standard library is used.
"""
import json
import os
import subprocess
import sys
import urllib.request

OLLAMA = "http://127.0.0.1:11434"

MODELS = ["qwen3.5:4b", "nemotron-3-nano:4b", "granite4.2:3b", "phi4-mini", "llama3.2:3b"]

TASKS = {
    "turkce": "API testi nedir? Turkce olarak 3 cumleyle anlat.",
    "log": (
        "These are lines from a web server log during a test run. "
        "List each error, its HTTP status, the endpoint, and the most likely cause. Be short.\n"
        'INFO: 192.168.1.190:40186 - "GET /favicon.ico HTTP/1.1" 404 Not Found\n'
        'INFO: 192.168.1.190:40196 - "POST /api/login HTTP/1.1" 401 Unauthorized\n'
        'INFO: 192.168.1.190:40198 - "POST /api/login HTTP/1.1" 200 OK\n'
        'INFO: 192.168.1.203:48416 - "GET /api/devices HTTP/1.1" 200 OK'
    ),
    "kod": (
        "Write a Python function count_errors(lines) that takes web server log lines like "
        '\'"GET /x HTTP/1.1" 404 Not Found\' and returns a dict that counts each 4xx and 5xx status code. '
        "Only the code, no explanation."
    ),
}


def generate(model, prompt):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def gpu_placement(model):
    # ollama ps line example: "llama3.2:3b  a80c...  2.9 GB  100% GPU  4096  4 minutes from now"
    for line in run(["ollama", "ps"]).splitlines():
        if line.startswith(model):
            for part in ("100% GPU", "CPU/GPU", "100% CPU"):
                if part in line:
                    return part
            return line.split("  ")[-3].strip() if "  " in line else "?"
    return "?"


def vram_mb():
    out = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).strip()
    return out.splitlines()[0] if out else "?"


def main():
    models = sys.argv[1:] or MODELS
    rows = []
    for model in models:
        os.makedirs(os.path.join("results", model.replace(":", "_")), exist_ok=True)
        for task, prompt in TASKS.items():
            try:
                r = generate(model, prompt)
            except Exception as e:  # model missing, server down, timeout
                print(f"{model:22} {task:7} FAILED: {e}")
                continue
            tps = r["eval_count"] / (r["eval_duration"] / 1e9) if r.get("eval_duration") else 0
            load_s = r.get("load_duration", 0) / 1e9
            where = gpu_placement(model)
            mem = vram_mb()
            path = os.path.join("results", model.replace(":", "_"), task + ".txt")
            with open(path, "w") as f:
                f.write(r.get("response", ""))
            rows.append((model, task, tps, load_s, where, mem))
            print(f"{model:22} {task:7} {tps:6.1f} tok/s  load {load_s:5.1f}s  {where:9}  VRAM {mem} MB")

    print("\n| Model | Task | tokens/s | Load (s) | Runs on | VRAM (MB) |")
    print("|---|---|---|---|---|---|")
    for model, task, tps, load_s, where, mem in rows:
        print(f"| {model} | {task} | {tps:.1f} | {load_s:.1f} | {where} | {mem} |")
    print("\nAnswers saved under results/. Read them and score quality by hand.")


if __name__ == "__main__":
    main()
