# NVIDIA Internal Competitive Response Playbook

**Document ID:** INT-NVDA-2026-003
**Classification:** Internal Competitive Intelligence (Adapted / Synthetic)
**Last Updated:** Q1 FY2026
**Author:** Competitive Strategy Team
**Note:** Synthetic document for EDRAK testing. Assessments are illustrative.

---

## 1. AMD (Instinct MI300X / MI325X / MI350)

- **Threat level:** High on inference workloads, Medium on training.
- **Their pitch:** Higher HBM capacity per GPU and lower price per GPU.
- **Our response:**
  - Lead with total cost of ownership, including networking, software and engineering time.
  - Highlight NVLink domain scale (72 GPUs) versus AMD's smaller scale-up domain.
  - Offer NIM-optimized inference benchmarks on customer's own models.
- **Internal weakness:** AMD memory capacity advantage is real on large-model inference.

## 2. Intel (Gaudi 3)

- **Threat level:** Low to Medium.
- **Their pitch:** Aggressive pricing and open Ethernet-based scale-out.
- **Our response:** Ecosystem depth, enterprise support, and Spectrum-X Ethernet as a direct counter.
- **Watch item:** Intel foundry partnerships that could affect supply dynamics.

## 3. Hyperscaler Custom Silicon (Google TPU, AWS Trainium, Microsoft Maia, Meta MTIA)

- **Threat level:** High, long-term.
- **Their pitch:** Lower cost per token on internal and select customer workloads.
- **Our response:**
  - Keep hyperscalers as partners: they remain our largest customers and also our largest potential competitors.
  - Make NVIDIA the default for third-party / enterprise workloads through CUDA and NIM portability.
  - Co-design roadmap commitments (Rubin) to stay one generation ahead.

## 4. Open-Source Software Alternatives (ROCm, OpenAI Triton, PyTorch compilers)

- **Threat level:** Medium.
- **Our response:** Contribute upstream, keep TensorRT-LLM performance leadership, and maintain 85%+ developer stickiness (see KR 1.3 in INT-NVDA-2026-001).

## 5. Win / Loss Summary (Last 4 Quarters, Synthetic)

| Competitor | Deals Contested | Win Rate | Primary Loss Reason |
|-----------|-----------------|----------|---------------------|
| AMD | 42 | 71% | Price per GPU, memory capacity |
| Intel | 18 | 89% | Price |
| Custom ASIC (in-house) | 11 | 55% | Workload specialization, cost per token |
