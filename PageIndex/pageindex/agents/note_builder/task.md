# Note Builder Task

Read the court document below and generate structured notes for use during adjudication.

The document text includes:
- `[PAGE N]` markers indicating page boundaries (use these for `page_index`)
- `[§ node_id | Section Title]` markers from PageIndex (use `node_id` and `anchor_title` from these)

## Instructions

1. Work through the document section by section.
2. For each section that warrants annotation, produce one or more notes.
3. Assign the correct `note_type`: `summary`, `flag`, `quote`, or `cross_ref`.
4. For `flag` notes, assign severity: `high`, `medium`, or `low`.
5. For `quote` notes, reproduce the exact text verbatim.
6. Order notes by `page_index` ascending.
7. Do not produce notes for boilerplate (cause title, court stamp text, standard prayer language).

---

## Document Text

{{document_text}}
