"""Feedback & Issues router module.

Provides endpoints for feedback submissions and GitHub issue creation.
"""

from __future__ import annotations

import asyncio
import json
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.schemas import (
    FeedbackResponse,
    FeedbackSubmitRequest,
    IssueCategory,
    build_github_issue_url,
)
from app.core.logger import log
from app.database import UserRecord
from app.routers.deps import (
    db,
    get_current_user_optional,
    perm_feedback_submit,
    settings,
)

router = APIRouter(tags=["Feedback & Issues"])


def render_feedback_markdown(req: FeedbackSubmitRequest) -> str:
    """Renders formatted Markdown matching GitHub issue templates from validated submission.

    Args:
        req: Validated feedback submission payload.

    Returns:
        str: Rendered Markdown body text.
    """
    if req.category == "bug_report":
        return (
            f"## 🐛 Expected Behavior\n{req.expected_behavior or 'N/A'}\n\n"
            f"## 💥 Current Behavior\n{req.current_behavior or 'N/A'}\n\n"
            f"## 📋 Steps to Reproduce\n{req.steps_to_reproduce or 'N/A'}\n\n"
            f"## 🖥️ Environment & Host Diagnostics\n{req.host_environment or 'N/A'}\n\n"
            f"## 📜 Diagnostic Logs & Tracebacks\n```text\n{req.diagnostic_logs or 'N/A'}\n```\n\n"
            f"## 🛠️ Possible Root Cause / Proposed Solution\n{req.proposed_solution or 'N/A'}\n"
        )
    if req.category == "feature_request":
        return (
            f"## 🚀 Feature Proposal\n{req.feature_proposal or req.title}\n\n"
            f"## 🎯 Problem / User Story\n{req.problem_user_story or 'N/A'}\n\n"
            f"## 💡 Proposed Solution & Architecture\n{req.description or 'N/A'}\n\n"
            f"## 🧱 12-Factor & Resilience Considerations\n{req.twelve_factor_considerations or 'N/A'}\n\n"
            f"## 🔄 Alternatives Considered\n{req.alternatives_considered or 'N/A'}\n"
        )
    if req.category == "documentation_update":
        return (
            f"## 📝 Documentation Area\n{req.documentation_area or 'N/A'}\n\n"
            f"## 🎯 Motivation & Missing Context\n{req.motivation_missing_context or 'N/A'}\n\n"
            f"## ✏️ Proposed Content / Diff\n{req.proposed_content or req.description or 'N/A'}\n"
        )
    if req.category == "security_report":
        return (
            f"## 🛡️ Security Vulnerability Summary\n{req.vulnerability_summary or req.description or 'N/A'}\n\n"
            f"## 🔍 Vulnerability Details & Attack Vector\n"
            f"- **Affected File & Line(s)**: {req.affected_files_lines or 'N/A'}\n"
            f"- **CWE Identifier**: {req.cwe_identifier or 'N/A'}\n"
            f"- **Severity**: {req.severity or 'Medium'}\n\n"
            f"## 💣 Proof of Concept / Reproduction Flow\n{req.poc_reproduction or 'N/A'}\n\n"
            f"## 🛡️ Recommended Remediation / Defensive Patch\n{req.recommended_remediation or 'N/A'}\n"
        )
    return req.description or req.title


async def dispatch_github_issue(
    category: str,
    title: str,
    body: str,
    feedback_id: int,
) -> None:
    """Asynchronously creates a GitHub issue via the GitHub CLI if configured.

    Args:
        category: Issue category matching repo templates.
        title: Issue title string.
        body: Markdown issue body content.
        feedback_id: Target feedback database primary key.
    """
    gh_bin = shutil.which("gh")
    if not gh_bin:
        log.debug("GitHub CLI (gh) not installed. Issue dispatch skipped for feedback #%d.", feedback_id)
        return

    label_map = {
        "bug_report": "bug",
        "feature_request": "enhancement",
        "documentation_update": "documentation",
        "security_report": "security",
    }
    label = label_map.get(category, "feedback")
    cmd = [gh_bin, "issue", "create", "--title", f"[{category.upper()}] {title}", "--body", body, "--label", label]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode == 0:
            output_str = stdout.decode("utf-8").strip()
            parts = output_str.split("/")
            if parts and parts[-1].isdigit():
                issue_num = int(parts[-1])
                await asyncio.to_thread(db.update_feedback_status, feedback_id, "OPEN", issue_num)
                log.info("Dispatched GitHub issue #%d for feedback #%d", issue_num, feedback_id)
    except asyncio.TimeoutError as err:
        log.warning("GitHub CLI issue dispatch timed out: %s", err)
    except OSError as err:
        log.debug("OS error running GitHub CLI issue dispatch: %s", err)
    except ValueError as err:
        log.debug("Value error parsing GitHub issue number: %s", err)


REDIRECT_CATEGORY_MAP: dict[str, IssueCategory] = {
    "bug": "bug_report",
    "bug_report": "bug_report",
    "feature": "feature_request",
    "feature_request": "feature_request",
    "docs": "documentation_update",
    "documentation": "documentation_update",
    "documentation_update": "documentation_update",
    "security": "security_report",
    "security_report": "security_report",
}


@router.get("/feedback/{category_shortcut}")
async def redirect_to_github_template(category_shortcut: str) -> RedirectResponse:
    """Redirects clients to the corresponding GitHub issue template or security advisory.

    Args:
        category_shortcut: Shortcut string ('bug', 'feature', 'docs', 'security').

    Returns:
        RedirectResponse: HTTP 307 temporary redirect to upstream GitHub repository.

    Raises:
        HTTPException: 404 Not Found if template shortcut is unrecognized.
    """
    category = REDIRECT_CATEGORY_MAP.get(category_shortcut.lower())
    if category is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unrecognized feedback category '{category_shortcut}'. "
            f"Valid categories: {sorted(REDIRECT_CATEGORY_MAP.keys())}",
        )
    target_url = build_github_issue_url(settings.github_repo_url, category)
    return RedirectResponse(url=target_url, status_code=307)


@router.post("/api/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackSubmitRequest,
    bg: BackgroundTasks,
    user: UserRecord = Depends(perm_feedback_submit),
) -> FeedbackResponse:
    """Records an issue or feedback submission mapped 1:1 to repository templates.

    Args:
        req: Template feedback submission payload.
        bg: FastAPI background tasks.
        user: Authenticated user.

    Returns:
        FeedbackResponse: The created feedback with ticket status.
    """
    markdown_body = render_feedback_markdown(req)
    metadata = req.model_dump(exclude={"category", "title", "description"}, exclude_none=True)
    metadata_json = json.dumps(metadata)

    feedback_rec = db.create_feedback(
        category=req.category,
        title=req.title,
        description=markdown_body,
        metadata_json=metadata_json,
        submitted_by=user.username,
    )

    bg.add_task(
        dispatch_github_issue,
        req.category,
        req.title,
        markdown_body,
        feedback_rec.id,
    )

    return FeedbackResponse(
        id=feedback_rec.id,
        category=feedback_rec.category,
        title=feedback_rec.title,
        description=feedback_rec.description,
        metadata=metadata,
        submitted_by=feedback_rec.submitted_by,
        status=feedback_rec.status,
        github_issue_number=feedback_rec.github_issue_number,
        created_at=feedback_rec.created_at,
    )


# pylint: disable=too-many-arguments,too-many-positional-arguments
# Rationale: API endpoint supports multi-field filtering, pagination, and user scoping.
@router.get("/api/feedback", response_model=list[FeedbackResponse])
async def list_feedback_submissions(
    request: Request,
    mine: bool = False,
    submitted_by: str | None = None,
    category: IssueCategory | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[FeedbackResponse]:
    """Lists historical feedback submissions with optional filtering.

    Args:
        request: Inbound FastAPI HTTP request.
        mine: Filter for tickets created by the active authenticated user.
        submitted_by: Optional filter for tickets submitted by a specific user handle.
        category: Optional filter for issue template category.
        status: Optional filter for ticket status ('OPEN', 'RESOLVED', 'CLOSED').
        limit: Max entries to return.
        offset: Query offset.

    Returns:
        list[FeedbackResponse]: List of FeedbackResponse items.

    Raises:
        HTTPException: 401 Unauthorized if mine=True and user is unauthenticated.
    """
    filter_user: str | None = submitted_by
    if mine:
        user_rec = get_current_user_optional(request)
        if user_rec is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required to filter submissions by current user.",
            )
        filter_user = user_rec.username

    records = db.list_feedbacks(
        limit=limit,
        offset=offset,
        submitted_by=filter_user,
        category=category,
        status=status,
    )
    result: list[FeedbackResponse] = []
    for rec in records:
        try:
            meta = json.loads(rec.metadata_json)
        except json.JSONDecodeError as err:
            log.debug("JSON decode error in feedback metadata: %s", err)
            meta = {}
        result.append(
            FeedbackResponse(
                id=rec.id,
                category=rec.category,
                title=rec.title,
                description=rec.description,
                metadata=meta,
                submitted_by=rec.submitted_by,
                status=rec.status,
                github_issue_number=rec.github_issue_number,
                created_at=rec.created_at,
            )
        )
    return result
