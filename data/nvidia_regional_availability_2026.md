# NVIDIA Regional Availability, Supply Chain Constraints & Lead Times (2026)

- **Document ID**: NV-INT-SUPPLY-2026-V1
- **Classification**: Internal Proprietary (Adapted / Synthetic)
- **Last Updated**: February 20, 2026
- **Author**: Global Supply Chain Operations & Regional Allocations Committee
- **Note**: Synthetic document for EDRAK testing and internal evaluation.

---

## 1. Global Allocation & Regional Availability Summary

As of Q1 FY2026, delivery schedules for NVIDIA GPU accelerators and clustered systems vary significantly across geographical operating regions due to export control compliances, substrate packaging throughput, and high-bandwidth memory (HBM3e) wafer quotas.

```
+----------------------------------------------------------------------------------------------------+
| REGION         | H100 STATUS   | H200 STATUS        | B200 STATUS         | GB200 NVL72 STATUS     |
+----------------+---------------+--------------------+---------------------+------------------------+
| United States  | Readily Avail | General Avail (GA) | Controlled Rollout  | Tier-1 Cloud Pre-alloc |
| Europe (EMEA)  | Readily Avail | General Avail (GA) | Staged Allocation   | Sampling / Lab Deploy  |
| Asia-Pacific   | Regional Regs | Sovereign / Partner| Selective Alloc     | Sovereign Gov Only     |
| Latin America  | Reseller Only | Partner Cloud      | Q4 FY2026 Forecast  | Not Planned for 2026   |
+----------------------------------------------------------------------------------------------------+
```

---

## 2. Regional Lead Times and Deployment Windows (Q1 FY2026)

### 2.1 United States & North America
- **H100 SXM5**: 2 to 4 weeks delivery from premier distributors (TD SYNNEX, Arrow, Ingram Micro).
- **H200 SXM5**: 6 to 8 weeks standard procurement lead time.
- **B200 SXM5**: 14 to 18 weeks order-to-delivery lead time for enterprise customers.
- **GB200 NVL72**: 22 to 26 weeks. 80% of volume is prioritized for hyperscalers (Microsoft Azure, AWS, Google Cloud, Meta).

### 2.2 Europe, Middle East & Africa (EMEA)
- **H100 SXM5**: Immediate to 4 weeks across major European hubs (Frankfurt, Dublin, Amsterdam, Paris).
- **H200 SXM5**: 6 to 8 weeks lead time. Strong demand from sovereign AI projects in France, Germany, and the UK.
- **B200 SXM5**: 16 to 20 weeks lead time. Shipments subject to EU-compliant telemetry auditing.
- **GB200 NVL72**: 26 to 32 weeks lead time. First European rack deployments scheduled for French Sovereign AI cluster (Mistral/CEA) and Deutsche Telekom AI Datacenter in Biere, Germany.
- **Middle East**: UAE and Saudi Arabia sovereign deployments operate under US Department of Commerce validated end-user authorizations (VEU).

### 2.3 Asia-Pacific (APAC)
- **Japan & South Korea**: H200 and B200 available under 10–14 week lead times to major CSPs (SoftBank, Naver).
- **Mainland China & Hong Kong**: Under US Export Administration Regulations (EAR), standard H100, H200, B200, and GB200 systems are **prohibited for export**. Shipments are restricted strictly to export-compliant models (H20 / L20) subject to strict bis-threshold TPP performance density caps.

---

## 3. Major Cloud Provider Instance Availability by Region

| Cloud Provider | Region / Location | Accelerator Family | Instance Type | Status | Effective Hourly Rate |
|----------------|-------------------|-------------------|---------------|--------|-----------------------|
| **AWS** | US East (N. Virginia) | 8× H100 SXM5 | `p5.48xlarge` | Generally Available | $98.32 / hr (on-demand)|
| **AWS** | US West (Oregon) | 8× H200 SXM5 | `p5en.48xlarge`| Generally Available | $124.50 / hr (on-demand)|
| **AWS** | Europe (Frankfurt) | 8× H100 SXM5 | `p5.48xlarge` | Generally Available | $108.15 / hr (on-demand)|
| **Azure** | East US | 8× H100 SXM5 | `ND96isr H100 v5` | Generally Available | $96.80 / hr (pay-as-you-go)|
| **Azure** | West Europe (NL) | 8× H200 SXM5 | `ND96isr H200 v5` | Generally Available | $129.40 / hr (pay-as-you-go)|
| **Azure** | Central US | GB200 NVL72 Rack | `NDv6 Blackwell` | Preview / Tier-1 Only | Custom Private Contract|
| **Google Cloud**| us-central1 (Iowa)| 8× H100 SXM5 | `a3-highgpu-8g` | Generally Available | $97.20 / hr (on-demand)|
| **Google Cloud**| europe-west4 (Eemshaven)| 8× H200 SXM5| `a3-megagpu-8g`| Rolling Out | $126.80 / hr (on-demand)|
| **Oracle OCI** | us-ashburn-1 | 8× H100 SXM5 | `BM.GPU.H100.8` | Generally Available | $80.00 / hr (bare metal)|
| **Oracle OCI** | eu-frankfurt-1 | 8× H200 SXM5 | `BM.GPU.H200.8` | Generally Available | $105.00 / hr (bare metal)|

---

## 4. Supply Chain Constraints & Component Bottlenecks

1. **TSMC CoWoS-L Advanced Packaging**:
   - Advanced dual-die packaging for Blackwell B200 and GB200 relies on TSMC CoWoS-L (Chip-on-Wafer-on-Substrate with Local Silicon Interconnect).
   - Packaging capacity is currently running at 94% utilization globally. TSMC Fab 18 and AP6 expansions will not ease pressure until Q3 FY2026.
2. **HBM3e Memory Sourcing (SK Hynix & Micron)**:
   - 141GB H200 and 192GB B200 require 8-high and 12-high HBM3e stacks running at 9.6 Gbps.
   - SK Hynix currently supplies 72% of NVIDIA's HBM3e volume, with Micron supplying 28%. Allocation remains tight through calendar year 2026.
3. **Liquid Cooling Infrastructure (CDU & Manifolds)**:
   - Rack-scale GB200 NVL72 deployments require liquid cooling loops with 120kW+ heat rejection capability per rack.
   - Global shortages in certified Coolant Distribution Units (CDUs) from partners (Vertiv, Schneider Electric, CoolIT) have lengthened data center readiness lead times by 8 to 12 weeks.

