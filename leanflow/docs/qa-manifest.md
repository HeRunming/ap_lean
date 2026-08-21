# QA manifest intake

LeanFlow accepts parser-produced JSON as a formalization source:

```bash
leanflow workflow formalize path/to/book.qa.json
leanflow workflow formalize path/to/book.qa.json --qa-batch chapter-1-batch-1
leanflow workflow formalize path/to/book.qa.json --qa-items 1.1,1.2
```

The preferred schema is:

```json
{
  "schema_version": "1",
  "book_title": "Example book",
  "items": [
    {
      "id": "chapter-2-exercise-7",
      "kind": "exercise",
      "page": 43,
      "source_locator": "book.pdf#page=43&bbox=...",
      "question": "Natural-language mathematical statement",
      "solution": "Optional natural-language solution",
      "dependencies": ["chapter-2-definition-3"]
    }
  ]
}
```

`qa_pairs`, `questions`, `problems`, and `data` are accepted aliases for
`items`. Statement aliases are `statement`, `question`, `problem`, `prompt`,
and `text`. Optional proof-hint aliases are `proof`, `solution`, `answer`,
`reference_answer`, and `rationale`.

The question is the statement source of truth. A supplied solution is retained
in the formalization blueprint as an optional prover hint; LeanFlow does not
require the generated Lean proof to reproduce its argument. Every accepted
item becomes a source-inventory entry with stable identity and provenance, and
is independently statement-reviewed before proof search.

Parser implementations should emit stable IDs and concrete page/bounding-box
locators. Dependencies should refer to stable IDs, not display titles. Keeping
the parser output immutable lets later statement and proof attempts share one
provenance record without rewriting extraction artifacts.

For legacy flattened exercise datasets, LeanFlow also recognizes the
`label/question/solution/sources` shape. When `visual_correction_audit.json`
exists beside the QA file and `crop_manifest.json` exists one directory above,
their PDF pages and bounding boxes are merged into the normalized provenance.
The source files remain unchanged.

Before applying either scope, LeanFlow indexes the complete QA corpus and writes
the book-level blueprint, manifest, typed dependency graph, and shared Lean
module scaffold. It also maintains `reuse-registry.json`; promotion requires
two project-verified consumers or an explicit source definition plus project
verification. Consequently, batch 2 can reuse decisions and verified code
from batch 1 without placing all source items in the model context. Regenerating
the corpus artifacts is deterministic and does not overwrite existing shared
Lean declarations.
