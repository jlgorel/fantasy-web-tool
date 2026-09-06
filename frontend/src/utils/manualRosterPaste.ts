import { ManualCatalogPlayer } from '../types/player';

export interface ManualPasteMatch {
  player: ManualCatalogPlayer;
  kind: 'exact' | 'fuzzy';
  score: number;
  source: string;
}

export interface ManualPastePreview {
  matches: ManualPasteMatch[];
  ambiguous: string[];
}

export const normalizePlayerText = (value: string): string => value
  .normalize('NFKD')
  .replace(/[’‘]/g, "'")
  .toLowerCase()
  .replace(/\b(jr|sr|ii|iii|iv)\.?\b/g, ' ')
  .replace(/[^a-z0-9]+/g, ' ')
  .trim()
  .replace(/\s+/g, ' ');

const levenshtein = (left: string, right: string): number => {
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let i = 1; i <= left.length; i += 1) {
    let diagonal = previous[0];
    previous[0] = i;
    for (let j = 1; j <= right.length; j += 1) {
      const above = previous[j];
      previous[j] = Math.min(
        previous[j] + 1,
        previous[j - 1] + 1,
        diagonal + (left[i - 1] === right[j - 1] ? 0 : 1),
      );
      diagonal = above;
    }
  }
  return previous[right.length];
};

const similarity = (left: string, right: string) => {
  const longest = Math.max(left.length, right.length);
  return longest ? 1 - (levenshtein(left, right) / longest) : 1;
};

const bestWindowScore = (name: string, line: string): number => {
  const nameTokens = name.split(' ');
  const lineTokens = line.split(' ');
  if (lineTokens.length < nameTokens.length) return similarity(name, line);
  let best = 0;
  for (let index = 0; index <= lineTokens.length - nameTokens.length; index += 1) {
    best = Math.max(best, similarity(name, lineTokens.slice(index, index + nameTokens.length).join(' ')));
  }
  return best;
};

const positionHintForLine = (line: string): string | null => {
  if (/^qb\b/.test(line)) return 'QB';
  if (/^rb\b/.test(line)) return 'RB';
  if (/^wr\b/.test(line)) return 'WR';
  if (/^te\b/.test(line)) return 'TE';
  if (/^k\b/.test(line)) return 'K';
  if (/^(d st|dst|def)\b/.test(line)) return 'DEF';
  return null;
};

const aliasesForPlayer = (player: ManualCatalogPlayer, name: string): string[] => {
  const tokens = name.split(' ').filter(Boolean);
  const aliases = new Set<string>([name]);
  if (player.position === 'DEF') {
    const nickname = tokens[tokens.length - 1];
    if (nickname) {
      aliases.add(nickname);
      aliases.add(`${nickname} d st`);
      aliases.add(`${nickname} dst`);
    }
    if (player.team) {
      const team = normalizePlayerText(player.team);
      aliases.add(team);
      aliases.add(`${team} d st`);
    }
  } else if (tokens.length >= 2) {
    const initial = tokens[0][0];
    aliases.add(`${initial} ${tokens.slice(1).join(' ')}`);
    aliases.add(`${initial} ${tokens[tokens.length - 1]}`);
    if (tokens.length >= 3) aliases.add(`${initial} ${tokens.slice(-2).join(' ')}`);
  }
  return Array.from(aliases).filter((alias) => alias.length >= 3);
};

export const previewManualRosterPaste = (
  text: string,
  catalog: ManualCatalogPlayer[],
): ManualPastePreview => {
  const normalizedCatalog = catalog
    .map((player) => {
      const name = normalizePlayerText(player.name);
      return { player, name, aliases: aliasesForPlayer(player, name) };
    })
    .filter(({ name }) => name.split(' ').length >= 2 && name.length >= 6);
  const lines = text.slice(0, 250_000).split(/\r?\n/).map(normalizePlayerText).filter(Boolean);
  const matches = new Map<string, ManualPasteMatch>();
  const ambiguous: string[] = [];

  for (const line of lines.slice(0, 5000)) {
    const padded = ` ${line} `;
    const positionHint = positionHintForLine(line);
    const compatible = normalizedCatalog.filter(({ player }) =>
      !positionHint || player.position === positionHint);
    const aliases = Array.from(new Set(compatible.flatMap((entry) => entry.aliases)))
      .filter((alias) => padded.includes(` ${alias} `))
      .sort((left, right) => right.length - left.length);
    let exactMatchFound = false;
    let ambiguousAlias = false;
    aliases.forEach((alias) => {
      const candidates = compatible.filter((entry) => entry.aliases.includes(alias));
      if (candidates.length === 1) {
        const { player } = candidates[0];
        matches.set(player.player_id, { player, kind: 'exact', score: 1, source: alias });
        exactMatchFound = true;
      } else if (candidates.length > 1) {
        ambiguousAlias = true;
      }
    });
    if (exactMatchFound) {
      continue;
    }
    if (ambiguousAlias) ambiguous.push(line);

    if (line.length < 6 || line.split(' ').length > 20) continue;
    const scored = compatible
      .map(({ player, name, aliases: playerAliases }) => ({
        player,
        name,
        score: Math.max(...playerAliases.map((alias) => bestWindowScore(alias, line))),
      }))
      .sort((left, right) => right.score - left.score);
    const best = scored[0];
    const runnerUp = scored[1];
    if (best && best.score >= 0.86 && best.score - (runnerUp?.score ?? 0) >= 0.06) {
      matches.set(best.player.player_id, {
        player: best.player,
        kind: 'fuzzy',
        score: best.score,
        source: line,
      });
    } else if (best && best.score >= 0.8) {
      ambiguous.push(line);
    }
  }

  return {
    matches: Array.from(matches.values()).sort((left, right) =>
      left.player.name.localeCompare(right.player.name)),
    ambiguous: Array.from(new Set(ambiguous)).slice(0, 25),
  };
};