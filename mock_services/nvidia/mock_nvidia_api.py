"""Mock REST API Server for NVIDIA Enterprise Services (OpenAPI 3.x)."""

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

app = FastAPI(
    title="NVIDIA Internal Enterprise Operations API",
    version="1.0.0",
    description="Operational microservices API for real-time cluster telemetry, pricing rules, and supply chain constraints.",
    servers=[{"url": "http://127.0.0.1:8088", "description": "Local Mock NVIDIA Enterprise Service"}],
)


class ClusterTelemetryResponse(BaseModel):
    cluster_status: str = Field(description="Operational health status of global compute clusters.")
    fleet_total_gpus: int = Field(description="Total GPU count across all global datacenters.")
    active_fleet_utilization_pct: float = Field(description="Current real-time fleet utilization percentage.")
    blackwell_ramp_units_deployed: int = Field(description="Number of newly deployed Blackwell B200/GB200 units.")
    power_efficiency_pue: float = Field(description="Global average Power Usage Effectiveness (PUE).")
    average_cluster_uptime_pct: float = Field(description="30-day trailing uptime percentage.")
    telemetry_timestamp: str = Field(description="ISO timestamp of telemetry snapshot.")


class DiscountTierRule(BaseModel):
    min_units: int
    max_discount_pct: float
    requires_vp_approval: bool
    approver: str


class PricingDiscountMatrixResponse(BaseModel):
    policy_version: str
    margin_floor_pct: float
    rules: List[DiscountTierRule]
    sovereign_ai_cap_pct: float
    notes: str


class SupplyChainLeadTimesResponse(BaseModel):
    as_of_quarter: str
    cowos_packaging_allocation_pct: float
    lead_times_weeks: Dict[str, int]
    bottlenecks: List[str]


@app.get(
    "/v1/telemetry/gpu-clusters",
    response_model=ClusterTelemetryResponse,
    summary="Retrieve Global GPU Cluster Telemetry",
    description="Returns live operational metrics on NVIDIA global compute cluster utilization, deployed Blackwell units, and uptime.",
)
def get_cluster_telemetry() -> ClusterTelemetryResponse:
    return ClusterTelemetryResponse(
        cluster_status="operational",
        fleet_total_gpus=680000,
        active_fleet_utilization_pct=91.4,
        blackwell_ramp_units_deployed=45000,
        power_efficiency_pue=1.18,
        average_cluster_uptime_pct=99.98,
        telemetry_timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get(
    "/v1/pricing/discount-matrix",
    response_model=PricingDiscountMatrixResponse,
    summary="Get Volume Discount Policy & Deal Desk Matrix",
    description="Returns current discount approval thresholds, volume tiers, and minimum margin floors enforced by the Deal Desk.",
)
def get_discount_matrix() -> PricingDiscountMatrixResponse:
    return PricingDiscountMatrixResponse(
        policy_version="FY2026.1",
        margin_floor_pct=70.0,
        rules=[
            DiscountTierRule(
                min_units=1000,
                max_discount_pct=8.0,
                requires_vp_approval=False,
                approver="Regional Sales Director",
            ),
            DiscountTierRule(
                min_units=10000,
                max_discount_pct=15.0,
                requires_vp_approval=True,
                approver="VP Deal Desk & Pricing",
            ),
        ],
        sovereign_ai_cap_pct=12.0,
        notes="All Blackwell rack sales above 500 GPUs require software subscription bundling.",
    )


@app.get(
    "/v1/supply-chain/lead-times",
    response_model=SupplyChainLeadTimesResponse,
    summary="Get Hardware Lead Times & Packaging Allocation",
    description="Returns estimated customer shipment lead times by GPU model and advanced CoWoS packaging allocation.",
)
def get_supply_chain_lead_times() -> SupplyChainLeadTimesResponse:
    return SupplyChainLeadTimesResponse(
        as_of_quarter="Q1 FY2026",
        cowos_packaging_allocation_pct=88.5,
        lead_times_weeks={
            "GB200 NVL72 rack": 22,
            "B200 GPU": 14,
            "H200 GPU": 6,
            "H100 GPU (legacy)": 2,
            "Spectrum-X switch bundle": 8,
        },
        bottlenecks=["TSMC CoWoS packaging capacity", "Liquid cooling manifold assemblies"],
    )


def dump_openapi_spec(output_path: str) -> None:
    """Exports the OpenAPI JSON specification to disk for offline / inline usage."""
    spec = app.openapi()
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
    print(f"Exported OpenAPI specification to: {output_path}")


if __name__ == "__main__":
    import uvicorn
    # Export spec first
    spec_path = os.path.join(os.path.dirname(__file__), "nvidia_openapi_spec.json")
    dump_openapi_spec(spec_path)
    # Run server
    uvicorn.run("mock_nvidia_api:app", host="127.0.0.1", port=8088, reload=False)
