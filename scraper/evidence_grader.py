from __future__ import annotations

from dataclasses import dataclass

from scraper.asset_classifier import (
    ASSET_TYPE_ARCHIVE,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_DOCUMENT,
    ASSET_TYPE_VIDEO,
)


@dataclass(slots=True)
class EvidenceGrade:
    tier: int
    rationale: str
    method: str = "rule_based_v1"


@dataclass(slots=True)
class EvidenceGradingConfig:
    enabled: bool
    government_domain_suffixes: list[str]
    tier1_asset_types: list[str]
    domain_tier_overrides: dict[str, int]


class EvidenceGrader:
    """Assign evidence tiers using conservative, traceable rule-based logic."""

    def __init__(self, config: EvidenceGradingConfig) -> None:
        self.config = config

    def grade(self, source_domain: str, asset_type: str, url: str) -> EvidenceGrade:
        if not self.config.enabled:
            return EvidenceGrade(
                tier=3, rationale="Grading disabled; default tier applied."
            )

        normalized_domain = source_domain.lower().strip()

        override_tier = self._domain_override_tier(normalized_domain)
        if override_tier is not None:
            return EvidenceGrade(
                tier=override_tier,
                rationale=f"Domain override for {normalized_domain}.",
            )

        if self._is_government_domain(normalized_domain) and asset_type in set(
            self.config.tier1_asset_types
        ):
            return EvidenceGrade(
                tier=1,
                rationale=(
                    "Government domain and primary-source-compatible asset type "
                    f"({asset_type})."
                ),
            )

        if asset_type in {
            ASSET_TYPE_DOCUMENT,
            ASSET_TYPE_VIDEO,
            ASSET_TYPE_AUDIO,
            ASSET_TYPE_ARCHIVE,
        }:
            return EvidenceGrade(
                tier=2,
                rationale="Traceable downloadable asset requiring source validation.",
            )

        return EvidenceGrade(
            tier=3,
            rationale=(
                "Defaulted to Tier 3 pending corroboration or manual review "
                f"for URL: {url}"
            ),
        )

    def _is_government_domain(self, source_domain: str) -> bool:
        return any(
            source_domain.endswith(suffix)
            for suffix in self.config.government_domain_suffixes
        )

    def _domain_override_tier(self, source_domain: str) -> int | None:
        for domain, tier in self.config.domain_tier_overrides.items():
            if source_domain.endswith(domain.lower()):
                return int(tier)
        return None
