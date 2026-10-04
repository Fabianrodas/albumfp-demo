import { Tag } from '../services/album-api';

/** Inserts `tag` into `tags` (alphabetically) unless it's already present. */
export function upsertTagSorted(tags: Tag[], tag: Tag): Tag[] {
  return tags.some(t => t.id === tag.id) ? tags : [...tags, tag].sort((a, b) => a.name.localeCompare(b.name));
}
