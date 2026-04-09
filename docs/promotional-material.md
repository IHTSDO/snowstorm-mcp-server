# SNOMED CT Connector for Claude — Promotional Material

## Headline

**Talk to SNOMED CT in plain English. Get answers backed by the world's most comprehensive clinical terminology.**

---

## Overview

The SNOMED CT Connector brings the full power of SNOMED CT directly into Claude. Whether you are a clinician, informaticist, terminology specialist, or developer building health applications, this connector lets you query, explore, and reason over clinical terminology using natural language without the need for any ECL expertise.

Backed by [Snowstorm](https://github.com/IHTSDO/snowstorm) and [Snowstorm Lite](https://github.com/IHTSDO/snowstorm-lite) — the reference SNOMED CT servers from SNOMED International — the connector surfaces live, release-quality terminology data for 29 international and national editions.

---

## What You Can Do

- **Look up any concept** by ID and get its full clinical detail: status, fully specified name, synonyms, hierarchy position, and defining attributes.
- **Expand value sets using ECL** — the Expression Constraint Language — without having to write the query yourself. Ask in plain English and Claude constructs and runs the ECL.
- **Browse the hierarchy** — find the direct children, ancestors, or all descendants of any concept in a single query.
- **Validate and check subsumption** — confirm whether a concept ID is active, or whether one concept is a subtype of another, directly from live terminology data.
- **Discover available editions** — see which SNOMED CT national and international editions are loaded on the connected server, and query against any of them.

---

## Who It Is For

| Audience | How They Use It |
|---|---|
| Clinical informaticists | Build and validate value sets, explore concept modelling, audit terminology coverage |
| Terminology specialists | Browse hierarchies, check modelling patterns, compare editions |
| Developers | Prototype SNOMED-backed features, test ECL queries, validate concept IDs in EHR pipelines |
| Clinicians and educators | Look up clinical concepts, understand classification rationale, explore synonyms |

---

## Featured Prompts and Screenshots

### 1. Discover Available Editions
> *What SNOMED CT editions are available on this server?*

Claude calls the connector and returns a structured summary of all 29 terminologies available on the server, grouped into Core/International editions (including Genomics, Veterinary, and Traditional Medicine extensions) and National Editions spanning 23 countries.

---

### 2. Look Up a Clinical Concept
> *Look up SNOMED CT concept 195967001 and tell me about it*

Claude retrieves full concept detail for Asthma (disorder) from the live International Edition — including active status, fully specified name, synonyms (Bronchial asthma, Airway hyperreactivity, and more), hierarchy position, 32 direct children, and effective date. It also flags a terminology modelling observation worth reviewing.

---

### 3. Expand a Value Set with ECL
> *Find all subtypes of asthma in SNOMED CT*

Claude runs a preflight count, confirms 127 strict subtypes within safe bounds, then fetches and renders them in an interactive filterable table organised by clinical theme: Severity, Persistence, Allergic/immunological, Exacerbation, Trigger/occupational, Clinical features, Control status, Co-occurring/context, and Treatment context.

---

### 4. Navigate the Hierarchy
> *What are the parent concepts of atrial fibrillation (concept 49436004) in SNOMED CT?*

Claude returns the three direct IS-A parents of Atrial fibrillation — Fibrillation, Atrial arrhythmia, and Supraventricular tachycardia — and explains the multi-parent modelling pattern, noting that each parent reflects a distinct valid clinical axis.

---

### 5. Test Subsumption
> *Is type 2 diabetes mellitus (concept 44054006) a type of diabetes mellitus (concept 73211009) in SNOMED CT?*

Claude invokes the FHIR `$subsumes` operation and confirms definitively that Diabetes mellitus subsumes Type 2 diabetes mellitus in the IS-A hierarchy, verified against the International Edition April 2026 release. It also explains what the result does and does not tell you about the relationship structure.

---

## Key Facts

- **15 tools** covering concept lookup, validation, subsumption, value set expansion, hierarchy navigation, and Snowstorm-native search
- **29 terminology editions** supported including International, US, UK, Australia, Canada, and 20+ national extensions
- **Read-only** — all tools carry `readOnlyHint: true`; the connector never writes to any terminology server
- **Live data** — queries run against the connected Snowstorm or Snowstorm Lite instance in real time
- **No account required** when connected to a public Snowstorm endpoint
- **Open source** — published by SNOMED International under the Apache 2.0 licence

---

## About SNOMED International

SNOMED International is the non-profit organisation that owns, develops, and distributes SNOMED CT. Used in electronic health records across 80+ countries, SNOMED CT is the world's most comprehensive multilingual clinical health terminology. This connector is the official MCP integration maintained by SNOMED International.

---

*All screenshots show live responses from a connected Snowstorm instance running the SNOMED CT International Edition, April 2026 release.*
