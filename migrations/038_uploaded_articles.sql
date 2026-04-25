-- 038: Add file upload metadata to documents table for tenant article uploads
-- Tracks uploaded file info vs hand-typed articles

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS source_file_name TEXT,
  ADD COLUMN IF NOT EXISTS source_file_type TEXT,
  ADD COLUMN IF NOT EXISTS file_size INTEGER;

COMMENT ON COLUMN documents.source_file_name IS 'Original filename of uploaded document (NULL for hand-typed articles)';
COMMENT ON COLUMN documents.source_file_type IS 'MIME type or extension of uploaded file (e.g. application/pdf, text/plain)';
COMMENT ON COLUMN documents.file_size IS 'File size in bytes of uploaded document';
