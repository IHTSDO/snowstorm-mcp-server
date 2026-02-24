from __future__ import annotations

from typing import Any

from .capabilities import probe_target
from .config import AppConfig, TargetConfig
from .fhir import SnomedLookupService
from .snowstorm_native import SnowstormNativeService
from .terminology import TerminologyInfo, TerminologyRegistry, build_registry


class UnsupportedBackendError(ValueError):
    pass


class ServerRuntime:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.registry: TerminologyRegistry = build_registry(config)

    def _resolve(self, terminology: str | None) -> tuple[TerminologyInfo, TargetConfig]:
        info = self.registry.resolve(terminology)
        target = self.registry.get_target(info.target_name)
        return info, target

    def list_terminologies(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "display_name": t.display_name,
                "backend_type": t.backend_type.value,
                "branch_path": t.branch_path,
            }
            for t in self.registry.list_terminologies()
        ]

    def server_health(self, terminology: str | None = None) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        status = probe_target(target)
        self.registry.set_target_status(info.target_name, status)
        return {
            "terminology": info.name,
            "backend_type": status.capabilities.backend_type.value,
            "branch_path": info.branch_path,
            "reachable": status.reachable,
            "base_url": status.base_url,
            "fhir_base_url": status.fhir_base_url,
            "error": status.error,
            "capabilities": {
                "has_fhir": status.capabilities.has_fhir,
                "has_native_api": status.capabilities.has_native_api,
                "has_lite_load_package": status.capabilities.has_lite_load_package,
            },
        }

    def server_capabilities(self, terminology: str | None = None) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        status = self.registry.get_target_status(info.target_name)
        if status is None:
            status = probe_target(target)
            self.registry.set_target_status(info.target_name, status)
        return {
            "terminology": info.name,
            "backend_type": status.capabilities.backend_type.value,
            "branch_path": info.branch_path,
            "base_url": status.base_url,
            "fhir_base_url": status.fhir_base_url,
            "reachable": status.reachable,
            "capabilities": {
                "has_fhir": status.capabilities.has_fhir,
                "has_native_api": status.capabilities.has_native_api,
                "has_lite_load_package": status.capabilities.has_lite_load_package,
            },
            "fhir_metadata_summary": _summarize_fhir_metadata(status.capabilities.fhir_metadata),
        }

    def fhir_metadata(
        self,
        terminology: str | None = None,
        *,
        include_raw: bool = True,
    ) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        status = self.registry.get_target_status(info.target_name)
        if status is None:
            status = probe_target(target)
            self.registry.set_target_status(info.target_name, status)
        payload = {
            "terminology": info.name,
            "fhir_base_url": status.fhir_base_url,
            "summary": _summarize_fhir_metadata(status.capabilities.fhir_metadata),
        }
        if include_raw:
            payload["metadata"] = status.capabilities.fhir_metadata
        return payload

    def snomed_lookup(
        self,
        *,
        terminology: str | None = None,
        code: str,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        with SnomedLookupService(target) as svc:
            result = svc.lookup(code=code, system=system, version=version)
        return {"terminology": info.name, **result.model_dump()}

    def snomed_validate_code(
        self,
        *,
        terminology: str | None = None,
        code: str,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        with SnomedLookupService(target) as svc:
            result = svc.validate_code(code=code, system=system, version=version)
        return {"terminology": info.name, **result.model_dump()}

    def snomed_subsumes(
        self,
        *,
        terminology: str | None = None,
        code_a: str,
        code_b: str,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        with SnomedLookupService(target) as svc:
            result = svc.subsumes(code_a=code_a, code_b=code_b, system=system, version=version)
        return {"terminology": info.name, **result.model_dump()}

    def snowstorm_search_concepts(
        self,
        *,
        terminology: str | None = None,
        term: str,
        limit: int = 10,
        active_only: bool = True,
    ) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        status = self.registry.get_target_status(info.target_name)
        if status and not status.capabilities.has_native_api:
            raise UnsupportedBackendError(
                f"Terminology '{info.name}' is on a backend that does not support "
                "Snowstorm native concept search."
            )
        branch = info.branch_path or "MAIN"
        with SnowstormNativeService(target) as svc:
            result = svc.search_concepts(
                term=term, branch=branch, limit=limit, active_only=active_only
            )
        return {"terminology": info.name, **result.model_dump()}

    def snowstorm_get_concept_native(
        self,
        *,
        terminology: str | None = None,
        concept_id: str,
        include_synonyms: bool = True,
        max_synonyms: int = 15,
    ) -> dict[str, Any]:
        info, target = self._resolve(terminology)
        status = self.registry.get_target_status(info.target_name)
        if status and not status.capabilities.has_native_api:
            raise UnsupportedBackendError(
                f"Terminology '{info.name}' is on a backend that does not support "
                "Snowstorm native concept detail."
            )
        branch = info.branch_path or "MAIN"
        with SnowstormNativeService(target) as svc:
            result = svc.get_concept(
                concept_id=concept_id,
                branch=branch,
                include_synonyms=include_synonyms,
                max_synonyms=max_synonyms,
            )
        return {"terminology": info.name, **result.model_dump()}


def _summarize_fhir_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    software = metadata.get("software") or {}
    return {
        "resourceType": metadata.get("resourceType"),
        "fhirVersion": metadata.get("fhirVersion"),
        "software_name": software.get("name"),
        "software_version": software.get("version"),
        "status": metadata.get("status"),
        "kind": metadata.get("kind"),
    }
