"""Public tool contracts and text formatting for the assignment connector."""

import json
from typing import Annotated, Literal

from mcp import types
from pydantic import BaseModel, Field


ResponseFormat = Annotated[Literal['json', 'markdown'], Field(
    description='Text format: json for processing or markdown for reading. Structured data is always returned.')]
CourseInput = Annotated[str, Field(min_length=1, max_length=200, pattern=r'\S',
    description='Course name or code, for example Networks or course-1. Ask the student if several courses match.')]
AssignmentInput = Annotated[str | None, Field(max_length=128, pattern=r'^[0-9a-f]{32}$',
    description='Opaque assignment_id returned by this tool for the selected course; omit to discover pending work.')]
StatusInput = Annotated[Literal['pending', 'overdue', 'both'], Field(
    description='Snapshot categories to list: pending (default), overdue, or both. Submitted work is excluded.')]
LimitInput = Annotated[int, Field(ge=1, le=100, strict=True,
    description='Maximum assignment rows across all requested categories, for example 20. Default 50, maximum 100.')]
OffsetInput = Annotated[int, Field(ge=0, strict=True,
    description='Rows to skip across pending then overdue categories. Start at 0; continue with next_offset.')]


class Course(BaseModel):
    code: str
    name: str


class AssignmentChoice(BaseModel):
    assignment_id: str
    title: str | None = None
    deadline: str | None = None


class SnapshotRow(BaseModel):
    title: str | None = None
    course_name: str | None = None
    deadline: str | None = None


class SnapshotOutput(BaseModel):
    status: Literal['ready']
    source: Literal['saved_assignment_snapshot']
    snapshot_at: str | None
    snapshot_age_seconds: int | None = Field(description='Age of the saved data; null means unknown. Always disclose this age.')
    snapshot_partial: bool
    deadline_timezone: Literal['Asia/Karachi']
    assignments: dict[str, list[SnapshotRow]]
    total_count: int = Field(ge=0, description='Total matching rows across all pages, not just this response.')
    category_counts: dict[str, int]
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    has_more: bool
    next_offset: int | None


class AssignmentOutput(BaseModel):
    status: Literal['ready', 'course_not_found', 'course_ambiguous', 'assignment_selection_required', 'no_pending_work']
    message: str | None = None
    course: Course | None = None
    courses: list[Course] | None = None
    assignment: AssignmentChoice | None = None
    assignments: list[AssignmentChoice] | None = None
    preparation_method: str | None = Field(default=None, description='Document preparation method; document text and images follow in content.')
    university_status_checked_live: bool | None = None
    server_elapsed_ms: int | None = None


SnapshotToolResult = Annotated[types.CallToolResult, SnapshotOutput]
AssignmentToolResult = Annotated[types.CallToolResult, AssignmentOutput]


def result_text(data: dict, response_format: ResponseFormat) -> str:
    """Format metadata only; document content remains separate and untrusted."""
    if response_format == 'json':
        return json.dumps(data, ensure_ascii=False)
    lines = [f"## LMS+ assignments: {data['status']}"]
    if data.get('message'):
        lines.append(data['message'])
    if data.get('source') == 'saved_assignment_snapshot':
        age = data['snapshot_age_seconds']
        lines.append('Snapshot age: unknown.' if age is None else f'Snapshot age: {age} seconds.')
        lines.append(f"Snapshot partial: {data['snapshot_partial']}. Deadlines: Asia/Karachi.")
        for category, rows in data['assignments'].items():
            lines.append(f'### {category.title()}')
            lines.extend(f"- {row.get('title')} — {row.get('course_name')} — {row.get('deadline')}" for row in rows)
            if not rows:
                lines.append('No rows on this page.')
        lines.append(f"Returned {data['count']} of {data['total_count']} matching assignments.")
        if data['has_more']:
            lines.append(f"Continue with offset={data['next_offset']} and the same status and limit.")
    else:
        if data.get('course'):
            course = data['course']
            lines.append(f"Course: {course['name']} ({course['code']})")
        for course in data.get('courses') or []:
            lines.append(f"- {course['name']} ({course['code']})")
        rows = data.get('assignments') or ([data['assignment']] if data.get('assignment') else [])
        lines.extend(f"- {row.get('title')} — {row.get('deadline')} (assignment_id: {row['assignment_id']})" for row in rows)
        if data.get('university_status_checked_live'):
            lines.append('Pending status checked live with the university.')
    return '\n\n'.join(lines)
