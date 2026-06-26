from __future__ import annotations

import io
import json
from html import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from .models import Assessment


def build_pdf_report(assessment: Assessment) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Kanokwere Ownership Assessment Report",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontSize=8.5,
            leading=11,
        )
    )
    story = [
        Paragraph("Kanokwere Ownership Assessment Report", styles["Title"]),
        Spacer(1, 8),
        Paragraph(
            "Knowledge of submitted work assessment", styles["Heading2"]
        ),
        Spacer(1, 10),
    ]

    summary_data = [
        ["Student", assessment.document.student_name],
        ["Student ID", assessment.document.student_id],
        ["Document", assessment.document.title],
        [
            "Course",
            f"{assessment.document.course.course_code} · {assessment.document.course.title}"
            if assessment.document.course
            else "Legacy or unassigned submission",
        ],
        ["File fingerprint", assessment.document.file_hash],
        ["Correct answers", f"{assessment.correct_count} of 20"],
        ["Score", f"{assessment.score:.1f}%"],
        ["Decision", assessment.decision or "Not available"],
        ["Focus losses recorded", str(assessment.focus_loss_count)],
        [
            "Webcam still image",
            "Captured" if assessment.webcam_snapshot and assessment.webcam_snapshot.image_data else "Not captured",
        ],
        [
            "Webcam captured at",
            assessment.webcam_snapshot.captured_at.isoformat()
            if assessment.webcam_snapshot and assessment.webcam_snapshot.captured_at
            else "Not available",
        ],
        ["Completed", assessment.completed_at.isoformat() if assessment.completed_at else "Not available"],
    ]
    table = Table(summary_data, colWidths=[42 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEADING", (0, 0), (-1, -1), 11),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([table, Spacer(1, 12)])
    story.append(
        Paragraph(
            "Interpretation: this result measures demonstrated knowledge of the submitted document. "
            "It is not conclusive proof of authorship or academic misconduct. A score below the "
            "institutional threshold should trigger further oral or manual verification.",
            styles["BodyText"],
        )
    )
    story.extend([PageBreak(), Paragraph("Question-level review", styles["Heading1"])])

    for item in sorted(assessment.items, key=lambda value: value.position):
        q = item.question
        outcome = "Correct" if item.is_correct else "Timed out" if item.timed_out else "Incorrect"
        response_time = f"{(item.response_ms or 0) / 1000:.1f} seconds"
        options = json.loads(item.shuffled_options_json)
        correct_answer = options[item.correct_shuffled_index]
        selected_answer = (
            options[item.selected_index]
            if item.selected_index is not None and 0 <= item.selected_index < len(options)
            else "No answer submitted"
        )
        story.extend(
            [
                Paragraph(escape(f"{item.position}. {q.stem}"), styles["Heading3"]),
                Paragraph(escape(f"Outcome: {outcome} | Response time: {response_time}"), styles["Small"]),
                Paragraph(escape(f"Selected answer: {selected_answer}"), styles["Small"]),
                Paragraph(escape(f"Correct answer: {correct_answer}"), styles["Small"]),
                Paragraph(escape(f"Source location: {q.source_location}"), styles["Small"]),
                Paragraph(escape(f"Supporting passage: {q.source_quote}"), styles["Small"]),
                Paragraph(escape(f"Explanation: {q.explanation}"), styles["Small"]),
                Spacer(1, 8),
            ]
        )

    document.build(story)
    return buffer.getvalue()
