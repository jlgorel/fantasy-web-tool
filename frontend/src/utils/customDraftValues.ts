import {
  CustomCsvMatch,
  CustomCsvPreview,
  CustomDraftSettings,
  RankingsPlayerRow,
} from '../types/draft';

const HEADER_ALIASES = {
  id: new Set(['playerid', 'sleeperid', 'id']),
  name: new Set(['player', 'playername', 'name']),
  position: new Set(['position', 'pos']),
  value: new Set(['value', 'vbd', 'vorp', 'val']),
};

function headerKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function normalizeDraftPlayerName(value: string): string {
  return (value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[’']/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter((token) => !['jr', 'sr', 'ii', 'iii', 'iv', 'v'].includes(token))
    .join(' ');
}

/** Minimal RFC-4180 parser: quoted commas, escaped quotes and CRLF are safe. */
export function parseCsvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  const source = (text || '').replace(/^\uFEFF/, '');

  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    if (quoted) {
      if (char === '"' && source[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ',') {
      row.push(field.trim());
      field = '';
    } else if (char === '\n') {
      row.push(field.trim());
      if (row.some((cell) => cell !== '')) rows.push(row);
      row = [];
      field = '';
    } else if (char !== '\r') {
      field += char;
    }
  }
  row.push(field.trim());
  if (row.some((cell) => cell !== '')) rows.push(row);
  return rows;
}

function findColumn(headers: string[], aliases: Set<string>): number {
  return headers.findIndex((header) => aliases.has(headerKey(header)));
}

export function previewCustomValuesCsv(
  text: string,
  players: RankingsPlayerRow[],
): CustomCsvPreview {
  const rows = parseCsvRows(text);
  if (rows.length < 2) {
    return { matches: [], errors: ['CSV must contain a header and at least one data row.'] };
  }
  const headers = rows[0];
  const idCol = findColumn(headers, HEADER_ALIASES.id);
  const nameCol = findColumn(headers, HEADER_ALIASES.name);
  const posCol = findColumn(headers, HEADER_ALIASES.position);
  const valueCol = findColumn(headers, HEADER_ALIASES.value);
  const errors: string[] = [];
  if (idCol < 0 && nameCol < 0) errors.push('Add a player_id or player/name column.');
  if (valueCol < 0) errors.push('Add a value, VBD, VORP, or VAL column.');
  if (errors.length) return { matches: [], errors };

  const byId: Record<string, RankingsPlayerRow> = {};
  const byName: Record<string, RankingsPlayerRow[]> = {};
  players.forEach((player) => {
    byId[player.player_id] = player;
    const key = normalizeDraftPlayerName(player.name);
    (byName[key] ||= []).push(player);
  });

  const seen = new Set<string>();
  const matches: CustomCsvMatch[] = rows.slice(1).map((cells, index) => {
    const inputId = idCol >= 0 ? (cells[idCol] || '').trim() : '';
    const inputName = nameCol >= 0 ? (cells[nameCol] || '').trim() : '';
    const inputPos = posCol >= 0 ? (cells[posCol] || '').trim().toUpperCase() : '';
    const rawValue = cells[valueCol];
    const value = Number(rawValue);
    const result: CustomCsvMatch = {
      row: index + 2,
      input_name: inputName || undefined,
      input_position: inputPos || undefined,
      input_player_id: inputId || undefined,
    };
    if (!Number.isFinite(value) || value < -10000 || value > 10000) {
      result.error = `Invalid value: ${rawValue || '(blank)'}`;
      return result;
    }
    result.value = value;

    let candidates: RankingsPlayerRow[] = [];
    if (inputId && byId[inputId]) {
      candidates = [byId[inputId]];
    } else if (inputName) {
      candidates = byName[normalizeDraftPlayerName(inputName)] || [];
      if (inputPos) candidates = candidates.filter((p) => p.pos === inputPos);
    }
    if (candidates.length === 0) {
      result.error = 'No exact player match';
      return result;
    }
    if (candidates.length > 1) {
      result.error = 'Ambiguous player name; add position or player_id';
      return result;
    }
    const player = candidates[0];
    if (seen.has(player.player_id)) {
      result.error = 'Duplicate player in CSV';
      return result;
    }
    seen.add(player.player_id);
    result.player_id = player.player_id;
    result.matched_name = player.name;
    return result;
  });
  return { matches, errors };
}

/** Parse the overall-ranks block copied from ElBoberto's CheatSheet tab.
 *
 * Users may copy the whole used sheet or only the four-column block. The
 * parser locates the contiguous ``OVR | Player | Pos | VBD`` header, preserves
 * the provider's finished VBD without recalculation, and conservatively maps
 * exact name+position pairs to the loaded Sleeper player pool.
 */
export function previewElBobertoValues(
  text: string,
  players: RankingsPlayerRow[],
): CustomCsvPreview {
  const rows = (text || '')
    .replace(/^\uFEFF/, '')
    .split(/\r?\n/)
    .map((line) => line.split('\t').map((cell) => cell.trim()))
    .filter((row) => row.some((cell) => cell !== ''));
  if (!rows.length || !rows.some((row) => row.length > 1)) {
    return {
      matches: [],
      errors: ['Paste the tab-separated Overall Ranks table from ElBoberto’s CheatSheet tab.'],
    };
  }

  const byNamePos: Record<string, RankingsPlayerRow[]> = {};
  players.forEach((player) => {
    const key = `${normalizeDraftPlayerName(player.name)}|${player.pos}`;
    (byNamePos[key] ||= []).push(player);
  });

  let headerRow = -1;
  let nameCol = -1;
  let positionCol = -1;
  let valueCol = -1;
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    const headers = rows[rowIndex].map(headerKey);
    for (let index = 0; index <= headers.length - 4; index += 1) {
      if (
        headers[index] === 'ovr'
        && headers[index + 1] === 'player'
        && headers[index + 2] === 'pos'
        && headers[index + 3] === 'vbd'
      ) {
        headerRow = rowIndex;
        nameCol = index + 1;
        positionCol = index + 2;
        valueCol = index + 3;
        break;
      }
    }
    if (headerRow >= 0) break;
  }
  if (headerRow < 0) {
    return {
      matches: [],
      errors: ['Could not find ElBoberto columns: OVR, Player, Pos, VBD. Copy from the CheatSheet tab.'],
    };
  }

  const seen = new Set<string>();
  const matches: CustomCsvMatch[] = [];
  rows.slice(headerRow + 1).forEach((cells, index) => {
    const inputName = (cells[nameCol] || '').trim();
    const position = (cells[positionCol] || '').trim().toUpperCase();
    if (!inputName || !['QB', 'RB', 'WR', 'TE'].includes(position)) return;
    const rawValue = (cells[valueCol] || '').trim();
    const value = Number(rawValue);
    const result: CustomCsvMatch = {
      row: headerRow + index + 2,
      input_name: inputName,
      input_position: position,
    };
    if (!Number.isFinite(value) || value < -10000 || value > 10000) {
      result.error = `Invalid value: ${rawValue || '(blank)'}`;
      matches.push(result);
      return;
    }
    result.value = value;

    const candidates = byNamePos[`${normalizeDraftPlayerName(inputName)}|${position}`] || [];
    if (candidates.length === 0) {
      result.error = 'No exact player and position match';
    } else if (candidates.length > 1) {
      result.error = 'Ambiguous player name and position';
    } else if (seen.has(candidates[0].player_id)) {
      result.error = 'Duplicate player in ElBoberto paste';
    } else {
      const player = candidates[0];
      seen.add(player.player_id);
      result.player_id = player.player_id;
      result.matched_name = player.name;
    }
    matches.push(result);
  });

  const errors: string[] = [];
  if (!matches.length) errors.push('No ElBoberto Overall Ranks player rows were found.');
  return { matches, errors };
}

export function emptyCustomDraftSettings(): CustomDraftSettings {
  return { version: 1, updated_at: new Date(0).toISOString(), entries: {} };
}

export function customDraftStorageKey(
  year: string,
  teams: number,
  ppr: number,
  superflex: boolean,
  profileSignature = 'default',
): string {
  return `draft-help-custom-v2:${year}:${teams}:${ppr}:${superflex ? 'sf' : '1qb'}:${profileSignature}`;
}

export function loadCustomDraftSettings(
  storage: Pick<Storage, 'getItem'>,
  key: string,
): CustomDraftSettings {
  try {
    const raw = storage.getItem(key);
    if (!raw) return emptyCustomDraftSettings();
    const parsed = JSON.parse(raw);
    if (parsed?.version !== 1 || typeof parsed.entries !== 'object' || !parsed.entries) {
      return emptyCustomDraftSettings();
    }
    const entries: CustomDraftSettings['entries'] = {};
    Object.entries(parsed.entries).slice(0, 500).forEach(([pid, rawEntry]) => {
      const entry = rawEntry as any;
      const value = Number(entry?.value);
      const hasValue = Number.isFinite(value) && value >= -10000 && value <= 10000;
      const avoid = entry?.avoid === true;
      if (!hasValue && !avoid) return;
      entries[pid] = {
        ...(hasValue ? { value } : {}),
        ...(avoid ? { avoid: true } : {}),
        source: entry?.source === 'elboberto_paste'
          ? 'elboberto_paste'
          : entry?.source === 'upload' || entry?.source === 'football_absurdity'
            ? 'upload'
            : 'manual',
      };
    });
    return {
      version: 1,
      updated_at: typeof parsed.updated_at === 'string'
        ? parsed.updated_at
        : new Date(0).toISOString(),
      entries,
    };
  } catch (_error) {
    return emptyCustomDraftSettings();
  }
}

export function saveCustomDraftSettings(
  storage: Pick<Storage, 'setItem'>,
  key: string,
  settings: CustomDraftSettings,
): void {
  storage.setItem(key, JSON.stringify(settings));
}

export function customValueMap(settings: CustomDraftSettings): Record<string, number> {
  const out: Record<string, number> = {};
  Object.entries(settings.entries).forEach(([pid, entry]) => {
    if (entry.value !== undefined) out[pid] = entry.value;
  });
  return out;
}

export function avoidedPlayerIds(settings: CustomDraftSettings): string[] {
  return Object.entries(settings.entries)
    .filter(([, entry]) => entry.avoid)
    .map(([pid]) => pid);
}
