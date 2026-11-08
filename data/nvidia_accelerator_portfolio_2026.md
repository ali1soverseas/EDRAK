# NVIDIA Accelerator Portfolio & Technical Specifications (2026)

- **Document ID**: NV-INT-SPEC-2026-V1
- **Classification**: Internal Proprietary (Adapted / Synthetic)
- **Last Updated**: February 15, 2026
- **Author**: Platform Architecture & Datacenter Product Group
- **Note**: Synthetic document for EDRAK testing and internal evaluation.

---

## 1. Executive Portfolio Overview

NVIDIA's accelerated computing architecture is built on three generations currently active across hyperscale and enterprise datacenters: **Ampere (A100)**, **Hopper (H100, H200)**, and **Blackwell (B200, GB200)**. 

Systems are categorized into two primary paradigms:
1. **Discrete GPU Accelerators**: SXM5 and PCIe modular boards integrated into OEM and ODM chassis.
2. **Integrated Rack-Scale Platforms**: Liquid-cooled and NVLink-switched clustered superchips such as the **GB200 NVL72**.

```
+-----------------------------------------------------------------------------------------------+
| GENERATION | PRODUCT      | FORM FACTOR | MEMORY          | BANDWIDTH | FP8 / BF16 TFLOPS     |
+------------+--------------+-------------+-----------------+-----------+-----------------------+
| Ampere     | A100 SXM4    | SXM4 / PCIe | 80GB HBM2e      | 2.0 TB/s  | N/A (312 BF16 TFLOPS) |
| Hopper     | H100 SXM5    | SXM5        | 80GB HBM2e      | 3.35 TB/s | 3,958 FP8 / 1,979 BF16|
| Hopper     | H200 SXM5    | SXM5        | 141GB HBM3e     | 4.8 TB/s  | 3,958 FP8 / 1,979 BF16|
| Blackwell  | B200 SXM5    | SXM5        | 192GB HBM3e     | 8.0 TB/s  | 9,000 FP8 / 4,500 BF16|
| Blackwell  | GB200 NVL72  | Rack Scale  | 13.8TB (aggregate) 576 TB/s aggregate | 1.44 ExaFLOPS FP8 |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. Detailed Technical Specifications by Product

### 2.1 NVIDIA H100 Tensor Core GPU (Hopper Architecture)
- **Compute Capability**: CUDA compute 9.0
- **Process Technology**: TSMC 4N customized process (80 billion transistors)
- **Memory Configuration**: 80GB HBM2e across 5 active stacks
- **Memory Bandwidth**: 3.35 TB/s
- **Interconnect**: NVLink 4.0 providing 900 GB/s bidirectional GPU-to-GPU bandwidth (18 links of 50 GB/s each)
- **Host Interface**: PCIe Gen5 x16 (128 GB/s bidirectional)
- **Thermal Design Power (TDP)**: 700W (SXM5 configurable 500W–700W), 350W (PCIe version)
- **Transformer Engine Performance**:
  - FP8 Tensor Core: 3,958 TFLOPS (with structural sparsity) / 1,979 TFLOPS (dense)
  - BF16/FP16 Tensor Core: 1,979 TFLOPS (sparse) / 989 TFLOPS (dense)
  - TF32 Tensor Core: 989 TFLOPS (sparse) / 494 TFLOPS (dense)
  - FP64 Tensor Core: 67 TFLOPS
  - INT8 Tensor Core: 3,958 TOPS (sparse)
- **Multi-Instance GPU (MIG)**: Up to 7 independent GPU instances with dedicated high-bandwidth memory, cache, and compute cores.

### 2.2 NVIDIA H200 Tensor Core GPU (Hopper Architecture)
- **Compute Capability**: CUDA compute 9.0
- **Process Technology**: TSMC 4N process
- **Memory Configuration**: 141GB HBM3e across 6 active stacks (24GB per stack)
- **Memory Bandwidth**: 4.8 TB/s (1.43x increase over H100)
- **Interconnect**: NVLink 4.0 with 900 GB/s bidirectional bandwidth
- **Host Interface**: PCIe Gen5 x16
- **Thermal Design Power (TDP)**: 700W (drop-in board replacement for H100 SXM5 chassis)
- **Key Differentiation**: Enables serving 70B parameter models (e.g., Llama 3) on a single GPU without tensor parallelism, cutting inference latency by 45% compared to H100 and halving TCO per token.

### 2.3 NVIDIA B200 Tensor Core GPU (Blackwell Architecture)
- **Compute Capability**: CUDA compute 10.0
- **Process Technology**: Dual TSMC 4NP die connected via 10 TB/s high-speed NV-HBI (High-Bandwidth Interconnect) chiplet interface (208 billion transistors aggregate)
- **Memory Configuration**: 192GB HBM3e (8 stacks of 24GB)
- **Memory Bandwidth**: 8.0 TB/s
- **Interconnect**: NVLink 5.0 delivering 1.8 TB/s bidirectional bandwidth per GPU (18 links of 100 GB/s)
- **Host Interface**: PCIe Gen6 ready (PCIe Gen5 x16 backward compatible)
- **Thermal Design Power (TDP)**: 700W – 1000W (1000W liquid-cooled; 700W air-cooled SXM5)
- **Blackwell Second-Generation Transformer Engine**:
  - FP4 Tensor Core: 18,000 TFLOPS (sparse) / 9,000 TFLOPS (dense)
  - FP8 Tensor Core: 9,000 TFLOPS (sparse) / 4,500 TFLOPS (dense)
  - BF16/FP16 Tensor Core: 4,500 TFLOPS (sparse) / 2,250 TFLOPS (dense)
  - FP64 Tensor Core: 80 TFLOPS
- **Key Innovations**: Hardware decompression engine (up to 800 GB/s), confidential computing at line rate, FP4 micro-tensor scaling.

### 2.4 NVIDIA GB200 NVL72 Platform (Rack-Scale Supercomputer)
- **Rack Architecture**: 18 compute trays containing 36 Grace CPUs and 72 Blackwell GPUs, linked via 9 NVLink switch trays with 18 NVLink 5.0 chips.
- **Topology**: Single unified 72-GPU NVLink domain operating as a massive virtual accelerator.
- **Aggregate Memory**: 13.8 TB of HBM3e + 17.2 TB of LPDDR5X system memory.
- **Aggregate NVLink Bandwidth**: 130 TB/s bisection bandwidth within the rack.
- **Cooling Architecture**: 100% liquid-cooled chassis requiring a dedicated Coolant Distribution Unit (CDU) with warm-water inlet (25°C to 32°C).
- **Rack Power Budget**: 120 kW to 135 kW per fully configured 42U/48U rack.
- **Peak Performance**: 1.44 ExaFLOPS FP8 inference, 720 PetaFLOPS BF16 training.

### 2.5 NVIDIA DGX H200 & HGX H200 Systems
- **DGX H200 Appliance**:
  - 8× NVIDIA H200 SXM5 GPUs (1,128 GB total HBM3e memory, 38.4 TB/s aggregate memory bandwidth).
  - Dual Intel Xeon Platinum 8570 or AMD EPYC 9654 processors (2 TB DDR5 system memory).
  - 8× NVIDIA ConnectX-7 400Gb/s InfiniBand/VPI adapters + 2× BlueField-3 DPUs.
  - Power: 10.2 kW redundant power supply subsystem.
- **HGX H200 Baseboard**: 8-way GPU baseboard provided to OEM partners (Dell, HPE, Supermicro, Lenovo, Cisco) for enterprise chassis integration.

### 2.6 Legacy Segment: NVIDIA A100 Tensor Core GPU (Ampere)
- **Compute Capability**: CUDA compute 8.0
- **Process Technology**: TSMC 7N (54 billion transistors)
- **Memory Configuration**: 40GB or 80GB HBM2e (2.0 TB/s bandwidth)
- **Interconnect**: NVLink 3.0 at 600 GB/s
- **Status**: End-of-Primary-Roadmap. Maintained for legacy maintenance contracts, entry enterprise workloads, and cost-sensitive cloud hosting.

---

## 3. Product Comparison & Selection Guide

| Dimension | A100 SXM4 | H100 SXM5 | H200 SXM5 | B200 SXM5 | GB200 NVL72 |
|-----------|-----------|-----------|-----------|-----------|-------------|
| **Architecture** | Ampere (2020) | Hopper (2022) | Hopper (2024) | Blackwell (2025) | Blackwell (2025) |
| **GPU Memory** | 80 GB HBM2e | 80 GB HBM2e | 141 GB HBM3e | 192 GB HBM3e | 72 × 192 GB (13.8 TB) |
| **Bandwidth** | 2.0 TB/s | 3.35 TB/s | 4.8 TB/s | 8.0 TB/s | 576 TB/s (aggregate) |
| **TDP (per GPU)** | 400W | 700W | 700W | 700W - 1000W | 120-135 kW (Rack) |
| **NVLink Rate** | 600 GB/s | 900 GB/s | 900 GB/s | 1,800 GB/s | 1,800 GB/s per GPU |
| **FP8 Tensor** | Not supported | 3,958 TFLOPS | 3,958 TFLOPS | 9,000 TFLOPS | 1,440,000 TFLOPS |
| **BF16 Tensor** | 312 TFLOPS | 1,979 TFLOPS | 1,979 TFLOPS | 4,500 TFLOPS | 720,000 TFLOPS |
| **Cooling** | Air or Liquid | Air or Liquid | Air or Liquid | Liquid or Air | Liquid Only (CDU required) |
| **Target Role** | Legacy CV/NLP | Frontier Training | LLM Inference | Frontier Trn & Inf | Trillion-Param Trn |

