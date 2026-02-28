from typing import Any

from esprit.tools.registry import register_tool


def calculate_cvss_and_severity(
    attack_vector: str,
    attack_complexity: str,
    privileges_required: str,
    user_interaction: str,
    scope: str,
    confidentiality: str,
    integrity: str,
    availability: str,
) -> tuple[float, str, str]:
    try:
        from cvss import CVSS3

        vector = (
            f"CVSS:3.1/AV:{attack_vector}/AC:{attack_complexity}/"
            f"PR:{privileges_required}/UI:{user_interaction}/S:{scope}/"
            f"C:{confidentiality}/I:{integrity}/A:{availability}"
        )

        c = CVSS3(vector)
        scores = c.scores()
        severities = c.severities()

        base_score = scores[0]
        base_severity = severities[0]

        severity = base_severity.lower()

    except Exception:
        import logging

        logging.exception("Failed to calculate CVSS")
        return 7.5, "high", ""
    else:
        return base_score, severity, vector


def _normalize_text(
    value: str | None,
    fallback: str,
    field_name: str,
    warnings: list[str],
) -> str:
    normalized = (value or "").strip()
    if normalized:
        return normalized
    warnings.append(f"{field_name} missing - defaulted")
    return fallback


def _normalize_cvss_component(
    value: str | None,
    field_name: str,
    valid_values: list[str],
    default_value: str,
    warnings: list[str],
) -> str:
    normalized = (value or "").strip().upper()
    if normalized in valid_values:
        return normalized
    if normalized:
        warnings.append(
            f"{field_name}={normalized} invalid - defaulted to {default_value}"
        )
    else:
        warnings.append(f"{field_name} missing - defaulted to {default_value}")
    return default_value


@register_tool(sandbox_execution=False)
def create_vulnerability_report(
    title: str | None = None,
    description: str | None = None,
    impact: str | None = None,
    target: str | None = None,
    technical_analysis: str | None = None,
    poc_description: str | None = None,
    poc_script_code: str | None = None,
    remediation_steps: str | None = None,
    # CVSS Breakdown Components
    attack_vector: str | None = None,
    attack_complexity: str | None = None,
    privileges_required: str | None = None,
    user_interaction: str | None = None,
    scope: str | None = None,
    confidentiality: str | None = None,
    integrity: str | None = None,
    availability: str | None = None,
    # Optional fields
    endpoint: str | None = None,
    method: str | None = None,
    cve: str | None = None,
    code_file: str | None = None,
    code_before: str | None = None,
    code_after: str | None = None,
    code_diff: str | None = None,
    cwe_id: str | None = None,
    owasp_category: str | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []

    title = _normalize_text(title, "Untitled vulnerability finding", "title", warnings)
    description = _normalize_text(
        description,
        "No description was provided by the agent output.",
        "description",
        warnings,
    )
    impact = _normalize_text(
        impact,
        "Potential security impact exists and should be reviewed manually.",
        "impact",
        warnings,
    )
    target = _normalize_text(target, "unknown target", "target", warnings)
    technical_analysis = _normalize_text(
        technical_analysis,
        "Technical analysis was not provided.",
        "technical_analysis",
        warnings,
    )
    poc_description = _normalize_text(
        poc_description,
        "Proof-of-concept description was not provided.",
        "poc_description",
        warnings,
    )
    poc_script_code = _normalize_text(
        poc_script_code,
        "N/A",
        "poc_script_code",
        warnings,
    )
    remediation_steps = _normalize_text(
        remediation_steps,
        "Review and remediate manually based on the findings.",
        "remediation_steps",
        warnings,
    )

    attack_vector = _normalize_cvss_component(
        attack_vector,
        "attack_vector",
        ["N", "A", "L", "P"],
        "N",
        warnings,
    )
    attack_complexity = _normalize_cvss_component(
        attack_complexity,
        "attack_complexity",
        ["L", "H"],
        "L",
        warnings,
    )
    privileges_required = _normalize_cvss_component(
        privileges_required,
        "privileges_required",
        ["N", "L", "H"],
        "N",
        warnings,
    )
    user_interaction = _normalize_cvss_component(
        user_interaction,
        "user_interaction",
        ["N", "R"],
        "N",
        warnings,
    )
    scope = _normalize_cvss_component(
        scope,
        "scope",
        ["U", "C"],
        "U",
        warnings,
    )
    confidentiality = _normalize_cvss_component(
        confidentiality,
        "confidentiality",
        ["N", "L", "H"],
        "L",
        warnings,
    )
    integrity = _normalize_cvss_component(
        integrity,
        "integrity",
        ["N", "L", "H"],
        "L",
        warnings,
    )
    availability = _normalize_cvss_component(
        availability,
        "availability",
        ["N", "L", "H"],
        "L",
        warnings,
    )

    cvss_score, severity, cvss_vector = calculate_cvss_and_severity(
        attack_vector,
        attack_complexity,
        privileges_required,
        user_interaction,
        scope,
        confidentiality,
        integrity,
        availability,
    )

    try:
        from esprit.telemetry.tracer import get_global_tracer

        tracer = get_global_tracer()
        if tracer:
            from esprit.llm.dedupe import check_duplicate

            existing_reports = tracer.get_existing_vulnerabilities()

            candidate = {
                "title": title,
                "description": description,
                "impact": impact,
                "target": target,
                "technical_analysis": technical_analysis,
                "poc_description": poc_description,
                "poc_script_code": poc_script_code,
                "endpoint": endpoint,
                "method": method,
            }

            dedupe_result = check_duplicate(candidate, existing_reports)

            if dedupe_result.get("is_duplicate"):
                duplicate_id = dedupe_result.get("duplicate_id", "")

                duplicate_title = ""
                for report in existing_reports:
                    if report.get("id") == duplicate_id:
                        duplicate_title = report.get("title", "Unknown")
                        break

                return {
                    "success": False,
                    "message": (
                        f"Potential duplicate of '{duplicate_title}' "
                        f"(id={duplicate_id[:8]}...). Do not re-report the same vulnerability."
                    ),
                    "duplicate_of": duplicate_id,
                    "duplicate_title": duplicate_title,
                    "confidence": dedupe_result.get("confidence", 0.0),
                    "reason": dedupe_result.get("reason", ""),
                }

            cvss_breakdown = {
                "attack_vector": attack_vector,
                "attack_complexity": attack_complexity,
                "privileges_required": privileges_required,
                "user_interaction": user_interaction,
                "scope": scope,
                "confidentiality": confidentiality,
                "integrity": integrity,
                "availability": availability,
            }

            report_id = tracer.add_vulnerability_report(
                title=title,
                description=description,
                severity=severity,
                impact=impact,
                target=target,
                technical_analysis=technical_analysis,
                poc_description=poc_description,
                poc_script_code=poc_script_code,
                remediation_steps=remediation_steps,
                cvss=cvss_score,
                cvss_breakdown=cvss_breakdown,
                endpoint=endpoint,
                method=method,
                cve=cve,
                code_file=code_file,
                code_before=code_before,
                code_after=code_after,
                code_diff=code_diff,
                cwe_id=cwe_id,
                owasp_category=owasp_category,
            )

            return {
                "success": True,
                "message": f"Vulnerability report '{title}' created successfully",
                "report_id": report_id,
                "severity": severity,
                "cvss_score": cvss_score,
                "warnings": warnings,
            }

        import logging

        logging.warning("Current tracer not available - vulnerability report not stored")

    except (ImportError, AttributeError) as e:
        return {"success": False, "message": f"Failed to create vulnerability report: {e!s}"}
    else:
        return {
            "success": True,
            "message": f"Vulnerability report '{title}' created (not persisted)",
            "warning": "Report could not be persisted - tracer unavailable",
            "warnings": warnings,
        }
