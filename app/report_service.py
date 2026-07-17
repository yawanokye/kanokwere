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
        title="Kanokware Learning Assurance Report",
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
        Paragraph("Kanokware Learning Assurance Report", styles["Title"]),
        Spacer(1, 8),
        Paragraph(
            "Understanding, process and assessment evidence", styles["Heading2"]
        ),
        Spacer(1, 10),
    ]

    summary_data = [
        ["Student", assessment.document.student_name],
        ["Student ID", assessment.document.student_id],
        ["Document", assessment.document.title],
        ["Submission stage", assessment.document.submission_stage.replace("_", " ").title()],
        ["Version", str(assessment.document.version_number)],
        ["AI-use declaration", assessment.document.ai_use_level.replace("_", " ").title()],
        [
            "Course",
            f"{assessment.document.course.course_code} · {assessment.document.course.title}"
            if assessment.document.course
            else "Legacy or unassigned submission",
        ],
        ["File fingerprint", assessment.document.file_hash],
        ["Correct answers", f"{assessment.correct_count} of {assessment.question_count or len(assessment.items)}"],
        ["Score", f"{assessment.score:.1f}%"],
        ["Decision", assessment.decision or "Not available"],
        ["Focus losses recorded", str(assessment.focus_loss_count)],
        ["Connection interruptions", str(assessment.interruption_count)],
        ["Successful resumes", str(assessment.resume_count)],
        ["Total offline time", f"{assessment.total_offline_seconds} seconds"],
        ["Interruption excused", "Yes" if assessment.interruption_excused else "No"],
        ["Interruption note", assessment.interruption_note or "Not provided"],
        ["Final connection status", assessment.status],
        ["Lock reason", assessment.lock_reason or "Not applicable"],
        ["Webcam warning events", str(len(assessment.monitoring_events or []))],
        [
            "Unresolved warning events",
            str(sum(1 for event in (assessment.monitoring_events or []) if not event.corrected)),
        ],
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
            "Interpretation: this report combines the student's justification, document-specific understanding "
            "assessment, monitoring evidence and any lecturer rubric mark. It is not conclusive proof of authorship "
            "or academic misconduct. Concerns should trigger proportionate oral, practical or manual verification.",
            styles["BodyText"],
        )
    )
    story.extend([Spacer(1, 12), Paragraph("Student justification and AI-use declaration", styles["Heading2"])])
    justification_data = [
        ["Understanding summary", assessment.document.understanding_summary or "Not provided"],
        ["Key decisions and reasons", assessment.document.key_decisions or "Not provided"],
        ["Main contribution or finding", assessment.document.main_contribution or "Not provided"],
        ["Limitation identified", assessment.document.stated_limitation or "Not provided"],
        ["AI-use level", assessment.document.ai_use_level.replace("_", " ").title()],
        ["AI-use details", assessment.document.ai_use_details or "No AI use declared"],
    ]
    justification_table = Table(
        [[Paragraph(f"<b>{escape(str(label))}</b>", styles["Small"]), Paragraph(escape(str(value)), styles["Small"])] for label, value in justification_data],
        colWidths=[42 * mm, 115 * mm],
    )
    justification_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(justification_table)

    rubric_mark = assessment.rubric_mark
    if rubric_mark:
        story.extend([Spacer(1, 12), Paragraph("Lecturer learning-assurance rubric", styles["Heading2"])])
        criteria = json.loads(rubric_mark.criteria_json)
        rubric_data = [["Criterion", "Score", "Comment"]]
        for criterion in criteria:
            rubric_data.append([
                criterion.get("label", criterion.get("key", "Criterion")),
                f"{criterion.get('score', 0):g}/{criterion.get('max_score', 0):g}",
                criterion.get("comment") or "",
            ])
        rubric_data.append(["Total", f"{rubric_mark.total_score:.1f}/100", rubric_mark.overall_comment or ""])
        rubric_table = Table(rubric_data, colWidths=[68 * mm, 28 * mm, 61 * mm], repeatRows=1)
        rubric_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(rubric_table)

    oral_defence = assessment.oral_defence
    if oral_defence:
        story.extend([Spacer(1, 12), Paragraph("Optional oral defence", styles["Heading2"])])
        oral_data = [
            ["Status", oral_defence.status.replace("_", " ").title()],
            ["Score", f"{oral_defence.total_score:.1f}%" if oral_defence.total_score is not None else "Not scored"],
            ["Outcome", (oral_defence.outcome or "Not recorded").replace("_", " ").title()],
            ["Notes", oral_defence.notes or "Not provided"],
        ]
        oral_table = Table(oral_data, colWidths=[42 * mm, 115 * mm])
        oral_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(oral_table)

    practical_tasks = list(assessment.practical_tasks or [])
    if practical_tasks:
        story.extend([Spacer(1, 12), Paragraph("Optional practical reproduction tasks", styles["Heading2"])])
        task_data = [["Task", "Score", "Status"]]
        for task in practical_tasks:
            task_data.append([
                task.prompt,
                f"{task.score:g}/{task.max_score:g}" if task.score is not None else "Not scored",
                task.status.replace("_", " ").title(),
            ])
        task_table = Table(task_data, colWidths=[112 * mm, 24 * mm, 27 * mm], repeatRows=1)
        task_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(task_table)

    version_analysis = assessment.document.version_analysis
    if version_analysis:
        analysis = json.loads(version_analysis.analysis_json)
        story.extend([Spacer(1, 12), Paragraph("Optional cross-version analysis", styles["Heading2"])])
        version_data = [
            ["Risk level", version_analysis.risk_level.title()],
            ["Textual similarity", f"{analysis.get('similarity_percent', 0)}%"],
            ["Word-count change", str(analysis.get("word_count_change", 0))],
            ["Inserted or replaced", f"{analysis.get('inserted_or_replaced_percent', 0)}%"],
            ["Flags", "; ".join(item.get("message", "") for item in analysis.get("flags", [])) or "No automated flags"],
        ]
        version_table = Table(version_data, colWidths=[42 * mm, 115 * mm])
        version_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(version_table)

    meetings = list(assessment.document.supervisor_meetings or [])
    if meetings:
        story.extend([Spacer(1, 12), Paragraph("Optional supervisor meeting records", styles["Heading2"])])
        meeting_data = [["Date", "Type", "Summary"]]
        for meeting in sorted(meetings, key=lambda row: row.meeting_at, reverse=True)[:10]:
            meeting_data.append([
                meeting.meeting_at.isoformat() if meeting.meeting_at else "Not available",
                meeting.meeting_type.replace("_", " ").title(),
                meeting.summary,
            ])
        meeting_table = Table(meeting_data, colWidths=[38 * mm, 32 * mm, 93 * mm], repeatRows=1)
        meeting_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(meeting_table)

    moderation = assessment.moderation_record
    if moderation:
        story.extend([Spacer(1, 12), Paragraph("Optional moderation record", styles["Heading2"])])
        moderation_data = [
            ["Status", moderation.status.replace("_", " ").title()],
            ["Selection", moderation.selected_reason],
            ["Original mark", f"{moderation.original_mark:.1f}"],
            ["Moderated mark", f"{moderation.moderated_mark:.1f}" if moderation.moderated_mark is not None else "Pending"],
            ["Variance", f"{moderation.variance:+.1f}" if moderation.variance is not None else "Pending"],
            ["Decision", (moderation.decision or "Pending").replace("_", " ").title()],
            ["Notes", moderation.notes or "Not provided"],
        ]
        moderation_table = Table(moderation_data, colWidths=[42 * mm, 115 * mm])
        moderation_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(moderation_table)

    alerts = list(assessment.document.learning_alerts or [])
    if alerts:
        story.extend([Spacer(1, 12), Paragraph("Learning intervention alerts", styles["Heading2"])])
        alert_data = [["Severity", "Alert", "Status"]]
        for alert in alerts:
            alert_data.append([alert.severity.title(), alert.message, alert.status.replace("_", " ").title()])
        alert_table = Table(alert_data, colWidths=[25 * mm, 105 * mm, 27 * mm], repeatRows=1)
        alert_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(alert_table)

    monitoring_events = list(assessment.monitoring_events or [])
    if monitoring_events:
        story.extend([Spacer(1, 12), Paragraph("Webcam monitoring events", styles["Heading2"])])
        monitoring_data = [["Time", "Question", "Event", "Duration", "Status"]]
        for event in monitoring_events:
            monitoring_data.append(
                [
                    event.created_at.isoformat() if event.created_at else "Not available",
                    str(event.question_position or "—"),
                    (event.message or event.event_type.replace("_", " ")).title(),
                    f"{event.duration_ms / 1000:.1f}s",
                    "Corrected" if event.corrected else "Needs review",
                ]
            )
        monitoring_table = Table(
            monitoring_data,
            colWidths=[39 * mm, 18 * mm, 55 * mm, 22 * mm, 28 * mm],
            repeatRows=1,
        )
        monitoring_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(monitoring_table)

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
                Paragraph(escape(f"Outcome: {outcome} | Response time: {response_time} | Category: {q.category.replace('_', ' ')}"), styles["Small"]),
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
