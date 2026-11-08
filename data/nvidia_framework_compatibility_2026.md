# NVIDIA Deep Learning Framework Compatibility & CUDA Driver Matrix (2026)

- **Document ID**: NV-INT-COMPAT-2026-V1
- **Classification**: Internal Proprietary (Adapted / Synthetic)
- **Last Updated**: February 10, 2026
- **Author**: AI Systems Software Integration Engineering
- **Note**: Synthetic document for EDRAK testing and internal evaluation.

---

## 1. Executive Summary

This reference document defines the certified compatibility matrix across major deep learning frameworks (PyTorch, TensorFlow, JAX, ONNX Runtime), NVIDIA GPU architectures (Ampere, Hopper, Blackwell), CUDA Toolkit releases, and required host display/datacenter drivers.

> **CRITICAL RULE**: Do not infer version compatibility solely from broad "CUDA-compatible" statements. Frameworks require exact runtime and compiler pairings to activate architectural extensions (such as Hopper DPX, TMA, and Blackwell FP4/FP8 micro-tensor engines).

---

## 2. Hardware Architecture & Minimum Driver Requirements

```
+-----------------------------------------------------------------------------------------------+
| GPU FAMILY          | ARCHITECTURE | MIN CUDA VERSION | MIN DATACENTER DRIVER | RECOMMENDED DRIVER|
+---------------------+--------------+------------------+-----------------------+-------------------+
| A100 (SXM4 / PCIe)  | Ampere       | CUDA 11.0        | 450.51.05             | 535.161.07 (LTS)  |
| H100 (SXM5 / PCIe)  | Hopper       | CUDA 11.8        | 525.60.13             | 535.183.01 (LTS)  |
| H200 (SXM5)         | Hopper       | CUDA 12.0        | 535.154.05            | 550.54.14         |
| B200 (SXM5)         | Blackwell    | CUDA 12.4        | 550.90.07             | 560.35.03         |
| GB200 NVL72         | Blackwell/Arm| CUDA 12.4        | 550.90.07             | 560.35.03         |
+-----------------------------------------------------------------------------------------------+
```

---

## 3. Major Machine Learning Framework Support Matrix

### 3.1 PyTorch (Production Baseline: PyTorch 2.3 & 2.4)
- **PyTorch 2.4.x**:
  - **CUDA Runtime**: Compiled with CUDA 12.4 by default.
  - **Hopper Support**: Full native support for FP8 via `torch.cuda.amp.autocast(dtype=torch.float8_e4m3fn)`. Flash-attention v3 integrated via `torch.nn.functional.scaled_dot_product_attention`.
  - **Blackwell Support**: Full instruction support for FP4 Tensor Cores via PyTorch AO (Architecture Optimization) library.
  - **Distribution**: Official wheels available on `download.pytorch.org/whl/cu124`.
- **PyTorch 2.3.x**:
  - **CUDA Runtime**: CUDA 12.1 / CUDA 11.8.
  - **Hopper Support**: Stable FP8 training and inference.
  - **Blackwell Support**: Requires source build with `TORCH_CUDA_ARCH_LIST="10.0"`.

### 3.2 TensorFlow (Production Baseline: TensorFlow 2.16 & 2.17)
- **TensorFlow 2.16 / 2.17**:
  - **CUDA Compatibility**: Requires CUDA 12.3 and cuDNN 9.1.
  - **Packaging**: Distributed via monolithic wheels with modular `nvidia-cudnn-cu12`, `nvidia-cublas-cu12` PIP dependencies.
  - **Hopper Acceleration**: XLA (Accelerated Linear Algebra) emits Hopper-optimized GEMM kernels via cuBLASLt.
  - **Blackwell Support**: Preliminary support; Blackwell-specific FP4 execution requires XLA Blackwell plugin available in NVIDIA Container 26.02-tf2-py3.

### 3.3 Google JAX (Production Baseline: JAX 0.4.30+)
- **JAX 0.4.30 / 0.4.31**:
  - **CUDA Backend**: Packaged as `jax[cuda12_pip]` with CUDA 12.3+ compatible XLA client.
  - **Hopper Support**: Native Pallas GPU kernels for Transformer Engine FP8 matmuls.
  - **Blackwell Support**: Experimental support for Blackwell dual-die layout via NVLink-C2C shared memory buffers.

### 3.4 ONNX Runtime (Production Baseline: ONNX Runtime 1.18+)
- **ONNX Runtime GPU 1.18 / 1.19**:
  - **Execution Providers**:
    - `TensorrtExecutionProvider` (TensorRT 10.2 backend for maximum throughput).
    - `CUDAExecutionProvider` (Direct CUDA 12.x / cuDNN 9.x invocation).
  - **Hardware Support**: Fully verified on A100, H100, and H200. Blackwell support verified on TensorRT EP version 10.3+.

---

## 4. Containerized Environment Matrix (NGC Catalog 2026)

NVIDIA delivers validated, turnkey software stacks monthly via the NVIDIA GPU Cloud (NGC) container registry:

| NGC Container Tag | Base OS | CUDA Version | Python | PyTorch / TF | Supported Accelerators |
|-------------------|---------|--------------|--------|--------------|------------------------|
| `nvcr.io/nvidia/pytorch:26.02-py3` | Ubuntu 24.04 | CUDA 12.4.1 | 3.11 | PyTorch 2.4.0a | A100, H100, H200, B200 |
| `nvcr.io/nvidia/tensorflow:26.02-tf2-py3` | Ubuntu 24.04 | CUDA 12.4.1 | 3.11 | TF 2.17.0 | A100, H100, H200, B200 |
| `nvcr.io/nvidia/tritonserver:26.02-py3` | Ubuntu 24.04 | CUDA 12.4.1 | 3.11 | Triton 2.45.0 | A100, H100, H200, B200 |
| `nvcr.io/nvidia/tensorrt-llm:0.11.0` | Ubuntu 22.04 | CUDA 12.4.0 | 3.10 | TRT-LLM 0.11.0 | H100, H200, B200 |

