-- Run once in Supabase SQL Editor.
-- Creates a private bucket for AI Self Learning MAX v14 checkpoints.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'learning-checkpoints',
  'learning-checkpoints',
  false,
  52428800,
  array['application/x-sqlite3', 'application/octet-stream', 'application/json']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

-- The bot uses SUPABASE_SERVICE_KEY, so no public storage policy is required.
