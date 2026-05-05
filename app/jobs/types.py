"""Job type constants. Handlers implemented across phases."""

# Phase-02
PARSE_BOOK = "parse_book"
SOFT_DELETE_BOOK = "soft_delete_book"
UPDATE_BOOK_PATHS = "update_book_paths"
SAVE_WEB = "save_web"

# Phase-03 (declared, handler added later)
INDEX_NOTE = "index_note"
INDEX_BOOK = "index_book"
EMBED_CHUNKS = "embed_chunks"

# Phase-07
INDEX_WEB_SAVED = "index_web_saved"

# Phase-04+
EXTRACT_TIER0 = "extract_tier0"
OPENIE_CANDIDATE = "openie_candidate"
WIKIDATA_ENRICH = "wikidata_enrich"
KGE_TRAIN = "kge_train"
