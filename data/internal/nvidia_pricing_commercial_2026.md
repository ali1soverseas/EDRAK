# NVIDIA Commercial Pricing, Cloud Rental Rates & Total Cost of Ownership (2026)

- **Document ID**: NV-INT-PRICE-2026-V1
- **Classification**: Internal Proprietary (Adapted / Synthetic)
- **Last Updated**: February 16, 2026
- **Author**: Pricing Strategy, Deal Desk & Cloud Commercial Operations
- **Note**: Synthetic document for EDRAK testing and internal evaluation.

---

## 1. Procurement Models Overview

NVIDIA products are consumed via three primary commercial models:
1. **Direct Hardware Capital Purchase (CapEx)**: Bare-metal chips, boards, and DGX turnkey systems purchased via OEM/distributor partners.
2. **Cloud Infrastructure-as-a-Service (OpEx)**: On-demand and reserved instances hosted by Tier-1 CSPs and specialized GPU clouds.
3. **Software & Microservices Subscriptions**: Enterprise software suites (NVIDIA AI Enterprise, NIM, NeMo) billed per GPU or per hour.

---

## 2. Direct Hardware Purchase (Average Selling Prices - ASP)

```
+------------------------------------------------------------------------------------------------+
| PRODUCT               | FORM FACTOR   | TARGET ASP (USD)      | TARGET GROSS MARGIN | VOLUME DISCOUNT  |
+-----------------------+---------------+-----------------------+---------------------+------------------+
| H100 SXM5             | SXM5 Board    | $18,000 - $22,000     | 65% - 68%           | Up to 8% (1k+)   |
| H200 SXM5             | SXM5 Board    | $25,000 - $30,000     | 70% - 73%           | Up to 10% (1k+)  |
| B200 SXM5             | SXM5 Board    | $30,000 - $35,000     | 72% - 75%           | Up to 15% (10k+) |
| DGX H200 SuperServer  | 8-GPU Chassis | $275,000 - $310,000   | 68% - 72%           | Up to 6% (10+)   |
| GB200 NVL72 Rack      | 72-GPU Rack   | $2,800,000 - $3,000,000| 74% - 76%          | Deal Desk Custom |
| Spectrum-X Switch Pod | Ethernet Pod  | $150,000 - $400,000   | 60% - 65%           | Bundled Tier     |
+------------------------------------------------------------------------------------------------+
```

### Volume Discount Matrix & Approval Gates
- **Tier 1 (100 – 999 units)**: Standard field sales discount up to 4%.
- **Tier 2 (1,000 – 9,999 units)**: Up to 8% discount; requires Regional Sales VP approval.
- **Tier 3 (10,000+ units)**: Up to 15% discount; requires Executive VP / CFO approval.
- **Gross Margin Floor**: Deal Desk strictly enforces an absolute gross margin floor of 70% across all hyperscaler volume quotes.

---

## 3. Cloud Provider Hourly Rental Rates (Market Reference)

Cloud instances provide immediate flexibility without hardware delivery lead times or datacenter buildout costs:

| Cloud Provider | Instance Type | GPU Configuration | Hourly Rate (On-Demand) | 1-Year Reserved | 3-Year Reserved |
|----------------|---------------|-------------------|-------------------------|-----------------|-----------------|
| **Amazon AWS** | `p5.48xlarge` | 8× H100 80GB SXM5 | $98.32 / hr ($12.29/GPU/hr)| $68.82 / hr | $44.24 / hr |
| **Amazon AWS** | `p5en.48xlarge`| 8× H200 141GB SXM5| $124.50 / hr ($15.56/GPU/hr)| $87.15 / hr| $56.03 / hr |
| **Microsoft Azure**| `ND96isr H100 v5`| 8× H100 80GB SXM5| $96.80 / hr ($12.10/GPU/hr)| $65.80 / hr | $42.59 / hr |
| **Microsoft Azure**| `ND96isr H200 v5`| 8× H200 141GB SXM5| $129.40 / hr ($16.18/GPU/hr)| $88.00 / hr| $58.20 / hr |
| **Google Cloud**| `a3-highgpu-8g`| 8× H100 80GB SXM5 | $97.20 / hr ($12.15/GPU/hr)| $66.10 / hr | $43.74 / hr |
| **Oracle OCI** | `BM.GPU.H100.8`| 8× H100 80GB SXM5 | $80.00 / hr ($10.00/GPU/hr)| $56.00 / hr | $36.00 / hr |
| **CoreWeave**  | Custom HGX H100 | 8× H100 80GB SXM5 | $63.84 / hr ($7.98/GPU/hr) | $47.88 / hr | $35.91 / hr |

---

## 4. Software Subscription Pricing: NVIDIA AI Enterprise

Software subscriptions provide critical enterprise IP, security maintenance, and production support:

- **NVIDIA AI Enterprise (NVAIE) 5.x**:
  - **List Price**: $4,500 per GPU per year (perpetual license option: $10,125 + $2,025/year support).
  - **Cloud Utility Model**: $1.00 per GPU hour across AWS, Azure, Google Cloud, and Oracle Marketplaces.
  - **Included Toolsets**: NVIDIA NIM microservices, NeMo Framework, Triton Inference Server Enterprise, RAPIDS Accelerator for Apache Spark, BioNeMo.
- **DGX Cloud Fully Managed Service**:
  - **Pricing**: $36,900 per node per month (includes 8× H100 or H200 GPUs, 3.2Tb/s fabric, and complete NVAIE software stack).

---

## 5. Total Cost of Ownership (TCO) Calculation Variables

When comparing direct hardware ownership versus cloud rentals, customers must account for full operational variables beyond bare GPU purchase price:

1. **Datacenter Power Costs**:
   - Power Consumption: An 8× H200 SXM5 system draws 10.2 kW peak power.
   - Operating Cost: At typical US industrial power rates ($0.09/kWh) with a PUE (Power Usage Effectiveness) of 1.25, electricity runs ~$10,050 per 8-GPU chassis annually.
2. **Cooling Infrastructure**:
   - Standard Hopper H100/H200 air-cooled systems require chilled air infrastructure.
   - Blackwell GB200 NVL72 racks require liquid cooling and external Coolant Distribution Units (CDUs), adding ~$150,000 per rack in capital facility upgrade costs.
3. **High-Speed Networking Fabrics**:
   - Clustered multi-node training requires 400Gb/s or 800Gb/s Quantum-2 InfiniBand or Spectrum-X Ethernet switches, optical transceivers, and active copper cables (ACC), which typically adds 18% to 25% of total compute hardware spend.
4. **Maintenance & Administration**:
   - System administrative overhead, hardware replacement sparing (0.5% failure allowance), and facility floor-space lease amortize to ~$800 to $1,200 per GPU annually.

