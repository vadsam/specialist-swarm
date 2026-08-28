---
name: solution-architecture
description: BTS-Synthetic reference architecture patterns for enterprise data platform proposals. Use whenever producing a proposed architecture for an RFP — covers lakehouse patterns, real-time ingest, BI integration, multi-region deployment, and integration with cloud providers. Trigger on any request to propose an architecture, draw a solution diagram, or assess technical fit against a specific workload.
---

# Solution Architecture Playbook

## Core reference architecture: Enterprise Lakehouse

```
                        ┌─────────────────────────────────┐
                        │         CLIENT SOURCES           │
                        │  IoT Devices │ ERP │ CRM │ Files │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │         INGEST LAYER             │
                        │  Real-time (Kafka/Event Hub)     │
                        │  Batch ETL (ADF / dbt)           │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │         STORAGE LAYER            │
                        │  Bronze │ Silver │ Gold          │
                        │  (Delta Lake / Iceberg)          │
                        │  Open formats: Parquet           │
                        └──────────────┬──────────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                   │
       ┌────────────▼───┐   ┌──────────▼──────┐  ┌───────▼────────┐
       │   BI & REPORT   │   │  SELF-SERVICE   │  │   ML / AI      │
       │  Power BI       │   │  Data prep      │  │  Feature store │
       │  DirectQuery    │   │  Notebooks      │  │  Model serving │
       └────────────────┘   └─────────────────┘  └───────────────┘
```

## Deployment patterns

### Multi-region (EU primary / US secondary)
- Active-active deployment for 99.99% SLA
- EU data residency: all EU customer data processed and stored in EU West (Amsterdam/Ireland)
- Async replication to US East for DR; no EU PII crosses region boundary
- Global load balancer routes analyst traffic to nearest region

### Cloud-native (Azure-first)
- Storage: Azure Data Lake Storage Gen2
- Compute: Azure Databricks (preferred) or Synapse Analytics
- Ingest: Azure Event Hubs for real-time; Azure Data Factory for batch
- BI: Power BI Premium with DirectQuery to Gold layer (no import latency)
- Security: Azure AD, Private Link, Customer-Managed Keys

### Hybrid (legacy + cloud)
- Teradata decommission path: Qlik Replicate or Attunity for CDC extraction
- Shadow run: 90-day parallel operation before cutover
- Validation harness: row counts + aggregation checksums at each migration milestone

## SLA architecture

| SLA target | Required pattern | Notes |
|---|---|---|
| 99.95% | Single-region active, multi-AZ | Our standard tier |
| 99.99% | Active-active multi-region | Requires separate rider; ~20% cost uplift |
| < 5s TTFT for BI | DirectQuery to Gold layer, aggregation tables | Avoid import mode for large datasets |

## Real-time ingest sizing

| Events/sec | Recommended pattern |
|---|---|
| < 10K | Azure Event Hubs Basic, single partition group |
| 10K – 100K | Event Hubs Standard, 32 partitions, Databricks Structured Streaming |
| > 100K | Event Hubs Premium or Kafka on AKS, dedicated streaming cluster |

For 80K events/sec (Acme-scale): Event Hubs Standard, 32 partitions, Databricks Structured Streaming writing Delta, 10-second micro-batch to Silver.

## Key design decisions to surface in every proposal

1. **Import vs DirectQuery for Power BI** — DirectQuery avoids data duplication and staleness but requires Gold layer query performance < 2s. Call this out explicitly; it determines whether you need aggregation tables.

2. **Medallion vs domain-oriented lakehouse** — Medallion (Bronze/Silver/Gold) is simpler for a single-team estate; domain-oriented (data mesh) is right when you have 5+ independent data domains. For most RFPs under $2M, recommend medallion and note the migration path.

3. **Open formats commitment** — Always propose Delta Lake (Linux Foundation) or Apache Iceberg. Never proprietary formats. This is the portability answer to lock-in objections.

4. **ML readiness** — If the RFP mentions ML (even "planned"), include a feature store stub in the architecture. Cost is low; it signals you've read the brief.

## How to present the architecture section

1. One-paragraph narrative: what the architecture does and why these choices for THIS customer
2. The diagram (simplified ASCII or description — the docx skill will render it)
3. Three key design decisions with the rationale
4. One honest gap or risk with the mitigation
5. Implementation phases: Phase 1 (foundation, 90 days), Phase 2 (BI cutover, 60 days), Phase 3 (ML enablement, 90 days)
