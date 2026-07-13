# External Memory Export/Import

## Export

Retrieve records via `external_memory_get` (by ID) or `external_memory_search` + `external_memory_get` (by topic/keywords). Write to a `.md` file (unless the user explicitly requested a different extension).

### File save

1. **Extension** — always `.md`, unless the user explicitly specifies otherwise.
2. **Path** — if not specified by the user, use the home directory (`~` or `$HOME`).
3. **Default name** — `memory_export.md`.
4. **Increment on conflict** — if the file already exists, add a suffix: `memory_export_1.md`, `memory_export_2.md`, etc. Check via `ls` or equivalent, find the first free number.
5. **After saving** — always report the full path of the created file to the user.

### Record Format

```
ID: <number>
Topic: <string>
Summary: <string>
Tags: <tag1>, <tag2>, ...
Content:
<multiline content text>

-------------------------
```

- Each field on its own line: `FieldName: value`
- `Content:` — on a line by itself with no value. EVERYTHING up to the blank line before `-------------------------` is treated as the Content body (may contain any characters, blank lines, colons)
- **A blank line before the separator is mandatory** — it marks the end of Content from the separator
- Separator: exactly 25 hyphens (`-------------------------`) on its own line

### Example

```
ID: 42
Topic: bug
Summary: PostgreSQL connection leak
Tags: bug, postgres
Content:
Root cause: pool did not return connection on timeout.
Fixed: defer pool.Release()

-------------------------
```

## Import

### 1. Parse the file
- Split the file into blocks by lines consisting of ≥25 consecutive hyphens
- Extract fields from each block: `ID`, `Topic`, `Summary`, `Tags`, `Content` (as described above)
- `Content` is optional (may be empty)
- A block missing `Topic` or `Summary` is corrupt — skip it, notify the user

### 2. Duplicate check
For each record: `external_memory_search(query="<Summary>", search_type="text")`. If results contain a record with an exactly matching `summary` — conflict.

### 3. Conflict resolution
On match, ask the user:
- **Replace** — delete the old one (`external_memory_delete_`), create a new one
- **Skip** — do not import
- **Keep both** — save both (just create a new one)

### 4. Create record

```json
external_memory_save({
  "topic":    "<Topic from file>",
  "summary":  "<Summary from file>",
  "content":  "<Content from file, as-is>",
  "tags":     [<Tags from file, split by comma, trim whitespace>]
})
```

The `ID` field from the file is ignored — the system assigns a new one.

### 5. Report
Output: successfully imported N, skipped M, corrupt K.
