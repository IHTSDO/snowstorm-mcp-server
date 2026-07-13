from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from .capabilities import BackendType, TargetStatus, probe_target
from .config import AppConfig, TargetConfig
from .http_client import HttpClient, HttpRequestError

logger = logging.getLogger(__name__)


class TerminologyInfo(BaseModel):
    """Represents a single routable terminology (SNOMED edition)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str | None = None
    target_name: str
    backend_type: BackendType
    branch_path: str | None = None


class TerminologyNotFoundError(KeyError):
    pass


class DuplicateTerminologyError(ValueError):
    pass


class DiscoveryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def discover_snowstorm_terminologies(
    target_name: str,
    target: TargetConfig,
    *,
    client: HttpClient | None = None,
) -> list[TerminologyInfo]:
    """Query GET /codesystems on a Snowstorm target and return one TerminologyInfo per code system."""
    own_client = client is None
    client = client or HttpClient(target)
    try:
        url = f"{target.base_url}/codesystems"
        data = client.request("GET", url, expect_json=True)
    except HttpRequestError as exc:
        raise DiscoveryError(
            f"Failed to discover terminologies on target '{target_name}': {exc}"
        ) from exc
    finally:
        if own_client:
            client.close()

    items = data.get("items", [])
    terminologies: list[TerminologyInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        short_name = item.get("shortName")
        if not isinstance(short_name, str) or not short_name.strip():
            continue
        branch_path = item.get("branchPath")
        if not isinstance(branch_path, str) or not branch_path.strip():
            continue
        display_name = item.get("name")
        terminologies.append(
            TerminologyInfo(
                name=short_name.strip().lower(),
                display_name=display_name if isinstance(display_name, str) else None,
                target_name=target_name,
                backend_type=BackendType.SNOWSTORM,
                branch_path=branch_path.strip(),
            )
        )
    return terminologies


def build_lite_terminology(
    target_name: str,
    target: TargetConfig,
) -> TerminologyInfo:
    """Build a TerminologyInfo for a Snowstorm Lite target from its config."""
    if not target.terminology_name:
        raise DiscoveryError(
            f"Lite target '{target_name}' must have 'terminology_name' configured."
        )
    return TerminologyInfo(
        name=target.terminology_name,
        display_name=None,
        target_name=target_name,
        backend_type=BackendType.LITE,
        branch_path=None,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TerminologyRegistry:
    """Central registry mapping terminology names to routing information."""

    def __init__(self) -> None:
        self._terminologies: dict[str, TerminologyInfo] = {}
        self._targets: dict[str, TargetConfig] = {}
        self._target_statuses: dict[str, TargetStatus] = {}
        self._default_terminology: str | None = None
        self._configured_default: str | None = None
        self._pending_discovery: dict[str, TargetConfig] = {}
        self._discovery_errors: dict[str, str] = {}

    @property
    def default_terminology(self) -> str | None:
        return self._default_terminology

    @property
    def discovery_errors(self) -> list[str]:
        return list(self._discovery_errors.values())

    def record_discovery_error(self, source: str, message: str) -> None:
        """Record a discovery problem keyed by its source (target name or
        'default_terminology') so it can be cleared if a later retry succeeds."""
        self._discovery_errors[source] = message

    def register(self, info: TerminologyInfo, target: TargetConfig) -> None:
        if info.name in self._terminologies:
            existing = self._terminologies[info.name]
            raise DuplicateTerminologyError(
                f"Terminology '{info.name}' is already registered "
                f"(target '{existing.target_name}'). "
                f"Cannot also register from target '{info.target_name}'."
            )
        self._terminologies[info.name] = info
        self._targets[info.target_name] = target

    def register_target(self, target_name: str, target: TargetConfig) -> None:
        """Record a target even before (or without) any terminology being
        registered for it, so tools can report on it by name."""
        self._targets[target_name] = target

    def set_configured_default(self, name: str | None) -> None:
        """Record the configured default terminology name so a later discovery
        retry can apply it once the terminology becomes available."""
        self._configured_default = name

    def set_default(self, name: str | None) -> None:
        if name is not None and name not in self._terminologies:
            available = ", ".join(sorted(self._terminologies.keys()))
            raise TerminologyNotFoundError(
                f"Default terminology '{name}' not found. Available: {available}"
            )
        self._default_terminology = name

    def resolve(self, terminology: str | None) -> TerminologyInfo:
        name = terminology
        if not name:
            if self._default_terminology is None:
                raise TerminologyNotFoundError(
                    "No terminology specified and no default_terminology configured. "
                    f"Available: {', '.join(sorted(self._terminologies.keys()))}"
                )
            name = self._default_terminology
        name = name.strip().lower()
        if name not in self._terminologies:
            raise TerminologyNotFoundError(
                f"Unknown terminology '{name}'. "
                f"Available: {', '.join(sorted(self._terminologies.keys()))}"
            )
        return self._terminologies[name]

    def get_target(self, target_name: str) -> TargetConfig:
        return self._targets[target_name]

    def list_terminologies(self) -> list[TerminologyInfo]:
        return sorted(self._terminologies.values(), key=lambda t: t.name)

    def list_terminology_names(self) -> list[str]:
        return sorted(self._terminologies.keys())

    def list_target_names(self) -> list[str]:
        return sorted(self._targets.keys())

    def resolve_for_target(
        self,
        *,
        target_name: str,
        terminology: str | None = None,
    ) -> TerminologyInfo:
        resolved_target = target_name.strip()
        if resolved_target not in self._targets:
            available_targets = ", ".join(self.list_target_names())
            raise TerminologyNotFoundError(
                f"Unknown target '{resolved_target}'. Available targets: {available_targets}"
            )

        if terminology:
            info = self.resolve(terminology)
            if info.target_name != resolved_target:
                raise TerminologyNotFoundError(
                    f"Terminology '{info.name}' is not served by target '{resolved_target}'. "
                    f"It is served by target '{info.target_name}'."
                )
            return info

        candidates = sorted(
            (t for t in self._terminologies.values() if t.target_name == resolved_target),
            key=lambda t: t.name,
        )
        if not candidates:
            raise TerminologyNotFoundError(
                f"Target '{resolved_target}' does not serve any discovered terminologies."
            )
        if self._default_terminology:
            default_info = self._terminologies.get(self._default_terminology)
            if default_info and default_info.target_name == resolved_target:
                return default_info
        if len(candidates) == 1:
            return candidates[0]
        options = ", ".join(c.name for c in candidates)
        raise TerminologyNotFoundError(
            f"Target '{resolved_target}' serves multiple terminologies: {options}. "
            "Specify a terminology to disambiguate."
        )

    def get_target_status(self, target_name: str) -> TargetStatus | None:
        return self._target_statuses.get(target_name)

    def set_target_status(self, target_name: str, status: TargetStatus) -> None:
        self._target_statuses[target_name] = status

    def mark_discovery_pending(self, target_name: str, target: TargetConfig) -> None:
        """Flag a target whose terminology discovery failed so it can be retried
        later without restarting the server."""
        self._pending_discovery[target_name] = target

    def has_pending_discovery(self) -> bool:
        return bool(self._pending_discovery)

    def retry_pending_discovery(self) -> bool:
        """Re-attempt terminology discovery for targets that failed at startup.

        Targets whose backend type could not be determined (e.g. the backend was
        down when the server started) are re-probed first. Returns True if at
        least one new terminology was registered. On success the target's
        discovery error is cleared and the configured default terminology is
        applied if it has become available.
        """
        registered_any = False
        for target_name, target_cfg in list(self._pending_discovery.items()):
            status = self.get_target_status(target_name)
            backend_type = (
                status.capabilities.backend_type if status else BackendType.UNKNOWN
            )
            if backend_type == BackendType.UNKNOWN:
                status = probe_target(target_cfg)
                self.set_target_status(target_name, status)
                backend_type = status.capabilities.backend_type
            if backend_type == BackendType.UNKNOWN:
                logger.debug(
                    "Discovery retry: backend type for target '%s' still unknown.",
                    target_name,
                )
                continue
            try:
                if backend_type == BackendType.LITE:
                    terminologies = [build_lite_terminology(target_name, target_cfg)]
                else:
                    terminologies = discover_snowstorm_terminologies(target_name, target_cfg)
            except DiscoveryError as exc:
                self.record_discovery_error(target_name, str(exc))
                logger.debug("Discovery retry failed for target '%s': %s", target_name, exc)
                continue
            del self._pending_discovery[target_name]
            self._discovery_errors.pop(target_name, None)
            for info in terminologies:
                try:
                    self.register(info, target_cfg)
                except DuplicateTerminologyError as exc:
                    self.record_discovery_error(target_name, str(exc))
                    logger.warning("Discovery retry for target '%s': %s", target_name, exc)
                    continue
                registered_any = True
            logger.info(
                "Terminology discovery recovered for target '%s': registered %d terminologies.",
                target_name,
                len(terminologies),
            )
        if registered_any:
            self._apply_default_after_recovery()
        return registered_any

    def _apply_default_after_recovery(self) -> None:
        if self._default_terminology is not None:
            return
        if self._configured_default:
            if self._configured_default in self._terminologies:
                self.set_default(self._configured_default)
                self._discovery_errors.pop("default_terminology", None)
        elif len(self._terminologies) == 1:
            self.set_default(next(iter(self._terminologies)))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_registry(config: AppConfig) -> TerminologyRegistry:
    """Build the TerminologyRegistry by probing targets and discovering terminologies."""
    registry = TerminologyRegistry()

    for target_name, target_cfg in config.targets.items():
        status = probe_target(target_cfg)
        registry.set_target_status(target_name, status)
        backend_type = status.capabilities.backend_type

        if backend_type == BackendType.SNOWSTORM:
            # Record the target up front so tools can report on it by name even
            # if discovery finds no terminologies or fails outright.
            registry.register_target(target_name, target_cfg)
            try:
                terminologies = discover_snowstorm_terminologies(target_name, target_cfg)
            except DiscoveryError as exc:
                logger.error(
                    "Terminology discovery failed for target '%s': %s. "
                    "No terminologies will be registered for this target until "
                    "discovery succeeds on a later retry. "
                    "Verify base_url and that GET %s/codesystems is reachable.",
                    target_name,
                    exc,
                    target_cfg.base_url,
                )
                registry.record_discovery_error(target_name, str(exc))
                # Do not invent a fake terminology that masks the
                # misconfiguration; flag the target for lazy re-discovery.
                registry.mark_discovery_pending(target_name, target_cfg)
                continue
            for info in terminologies:
                registry.register(info, target_cfg)

        elif backend_type == BackendType.LITE:
            info = build_lite_terminology(target_name, target_cfg)
            registry.register(info, target_cfg)

        else:
            detail = f": {status.error}" if status.error else ""
            registry.register_target(target_name, target_cfg)
            registry.record_discovery_error(
                target_name,
                f"Target '{target_name}' backend type could not be determined"
                f"{detail}. It will be re-probed when terminologies are next "
                "requested.",
            )
            registry.mark_discovery_pending(target_name, target_cfg)

    default = config.default_terminology
    if default:
        normalized_default = default.strip().lower()
        registry.set_configured_default(normalized_default)
        try:
            registry.set_default(normalized_default)
        except TerminologyNotFoundError:
            available = ", ".join(registry.list_terminology_names()) or "(none)"
            logger.warning(
                "default_terminology '%s' not found (available: %s). "
                "This usually means the backend was unreachable during startup "
                "and terminology auto-discovery failed. "
                "The server will start without a default terminology.",
                default,
                available,
            )
            registry.record_discovery_error(
                "default_terminology",
                f"default_terminology '{default}' not found; continuing without a default",
            )
    elif len(registry._terminologies) == 1:
        only_name = next(iter(registry._terminologies))
        registry.set_default(only_name)

    return registry
