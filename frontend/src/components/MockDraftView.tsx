/**
 * Mock snake-draft board + Monte-Carlo pick recommender.
 *
 * Load a value board for a league config, then click players to draft them to
 * whichever team is on the clock (snake order is tracked automatically). When
 * it's your pick, "Recommend my pick" runs the backend Monte-Carlo sim
 * (/draft-help/sim) — it predicts who'll be available at your later picks via
 * ADP and scores the starting lineup you'd end up with for each candidate.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Accordion,
  AccordionButton,
  AccordionIcon,
  AccordionItem,
  AccordionPanel,
  Badge,
  Box,
  Button,
  Checkbox,
  Heading,
  HStack,
  Input,
  Select,
  SimpleGrid,
  Spinner,
  Table,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tooltip,
  Tr,
  VStack,
} from '@chakra-ui/react';

import { api } from '../api/client';
import DraftPlayerAvatar from './DraftPlayerAvatar';
import PlayerCombobox from './PlayerCombobox';
import {
  CustomCsvPreview,
  CustomDraftSettings,
  RankingsPlayerRow,
  RankingsResponse,
  SimResponse,
} from '../types/draft';
import {
  avoidedPlayerIds,
  customDraftStorageKey,
  customValueMap,
  emptyCustomDraftSettings,
  loadCustomDraftSettings,
  previewCustomValuesCsv,
  saveCustomDraftSettings,
} from '../utils/customDraftValues';
import {
  buildRosterSlots,
  POS_COLOR,
  SLOT_COLOR,
} from '../utils/draftRoster';
import { confidencePresentation } from '../utils/simConfidence';

const YEARS = ['2026', '2025', '2024', '2023', '2022'];
const TEAM_SIZES = [8, 10, 12, 14];
const MIN_ADP_ONLY_VALUES = 50;
const PPRS = [
  { label: '0 PPR', value: 0 },
  { label: '0.5 PPR', value: 0.5 },
  { label: '1 PPR', value: 1 },
];

// Editable starting-lineup slots (drives which positions the sim prioritizes).
// SUPER_FLEX is added automatically from the Superflex checkbox.
const SLOT_KEYS: Array<{ key: string; label: string }> = [
  { key: 'QB', label: 'QB' },
  { key: 'RB', label: 'RB' },
  { key: 'WR', label: 'WR' },
  { key: 'TE', label: 'TE' },
  { key: 'FLEX', label: 'FLEX' },
];

function snakeSlot(pick: number, teams: number): number {
  const rnd = Math.floor((pick - 1) / teams);
  const idx = (pick - 1) % teams;
  return rnd % 2 === 0 ? idx + 1 : teams - idx;
}

interface HistoryEntry { pick: number; pid: string; slot: number; mine: boolean; }

const MockDraftView: React.FC = () => {
  // Config
  const [year, setYear] = useState('2026');
  const [teams, setTeams] = useState(12);
  const [rounds, setRounds] = useState(15);
  const [mySlot, setMySlot] = useState(1);
  const [ppr, setPpr] = useState(0.5);
  const [superflex, setSuperflex] = useState(false);
  const [starterSlots, setStarterSlots] = useState<Record<string, number>>(
    { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 },
  );

  // Board + draft state
  const [players, setPlayers] = useState<RankingsPlayerRow[] | null>(null);
  const [sources, setSources] = useState<RankingsResponse['sources']>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafted, setDrafted] = useState<Record<string, number>>({}); // pid -> slot
  const [myRoster, setMyRoster] = useState<string[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [posFilter, setPosFilter] = useState('ALL');
  const [boardSort, setBoardSort] = useState<'adp' | 'vbd'>('adp');

  // Browser-local custom values. They are scoped to season/config because a
  // VORP number is only meaningful under the league settings that produced it.
  const [customSettings, setCustomSettings] = useState<CustomDraftSettings>(
    emptyCustomDraftSettings(),
  );
  const [csvPreview, setCsvPreview] = useState<CustomCsvPreview | null>(null);
  const [manualPlayerId, setManualPlayerId] = useState('');
  const [manualValue, setManualValue] = useState('');
  const [customMessage, setCustomMessage] = useState<string | null>(null);

  // Sim
  const [sim, setSim] = useState<SimResponse | null>(null);
  const [simLoading, setSimLoading] = useState(false);

  const currentPick = history.length + 1;
  const totalPicks = teams * rounds;
  const onClock = snakeSlot(currentPick, teams);
  const isMyPick = onClock === mySlot;
  const draftOver = currentPick > totalPicks;

  const storageKey = useMemo(
    () => customDraftStorageKey(year, teams, ppr, superflex),
    [year, teams, ppr, superflex],
  );

  useEffect(() => {
    const loaded = typeof window === 'undefined'
      ? emptyCustomDraftSettings()
      : loadCustomDraftSettings(window.localStorage, storageKey);
    setCustomSettings(loaded);
    setCsvPreview(null);
    setCustomMessage(null);
    setSim(null);
  }, [storageKey]);

  const persistCustomSettings = (settings: CustomDraftSettings) => {
    const updated = { ...settings, updated_at: new Date().toISOString() };
    setCustomSettings(updated);
    if (typeof window !== 'undefined') {
      saveCustomDraftSettings(window.localStorage, storageKey, updated);
    }
    setSim(null);
  };

  const effectivePlayers = useMemo(() => (players || []).map((player) => {
    const custom = customSettings.entries[player.player_id];
    return custom?.value === undefined ? player : { ...player, vbd: custom.value };
  }), [players, customSettings]);

  const valueOverrides = useMemo(
    () => customValueMap(customSettings),
    [customSettings],
  );
  const avoidIds = useMemo(
    () => avoidedPlayerIds(customSettings),
    [customSettings],
  );
  const priorityCandidateIds = useMemo(
    () => Object.entries(customSettings.entries)
      .filter(([, entry]) => entry.source === 'manual' && entry.value !== undefined)
      .map(([pid]) => pid),
    [customSettings],
  );
  const usableValueCount = effectivePlayers.filter((player) => player.vbd != null).length;
  const isAdpOnly = sources?.values?.source === 'custom upload required';
  const hasUsableValues = isAdpOnly
    ? usableValueCount >= MIN_ADP_ONLY_VALUES
    : usableValueCount > 0;

  const byId = useMemo(() => {
    const m: Record<string, RankingsPlayerRow> = {};
    effectivePlayers.forEach((p) => { m[p.player_id] = p; });
    return m;
  }, [effectivePlayers]);

  const available = useMemo(() => {
    const list = effectivePlayers
      .filter((p) => drafted[p.player_id] === undefined)
      .filter((p) => posFilter === 'ALL' || p.pos === posFilter);
    // ADP sort = draft opponents realistically; VBD sort (via overall_rank,
    // which is assigned VBD-desc) = see the best value still on the board.
    list.sort(boardSort === 'adp'
      ? (a, b) => (a.adp ?? 9999) - (b.adp ?? 9999)
      : (a, b) => (b.vbd ?? -9999) - (a.vbd ?? -9999));
    return list.slice(0, 180);
  }, [effectivePlayers, drafted, posFilter, boardSort]);

  const rosterSlots = useMemo(
    () => buildRosterSlots(
      myRoster,
      byId,
      { ...starterSlots, ...(superflex ? { SUPER_FLEX: 1 } : {}) },
      rounds,
    ),
    [myRoster, byId, starterSlots, superflex, rounds],
  );
  const confidence = confidencePresentation(sim?.recommendation_confidence);

  const loadBoard = async () => {
    setLoading(true);
    setError(null);
    setSim(null);
    try {
      const resp = await api.getDraftHelpRankings(year, teams, ppr, superflex);
      if (!resp.players.length) {
        setError('No rankings available for that season/config.');
      }
      setPlayers(resp.players);
      setSources(resp.sources);
      setDrafted({});
      setMyRoster([]);
      setHistory([]);
    } catch (e) {
      setError('Failed to load the board.');
    } finally {
      setLoading(false);
    }
  };

  const draftPlayer = (pid: string) => {
    if (draftOver || drafted[pid] !== undefined) return;
    const slot = onClock;
    const mine = slot === mySlot;
    setDrafted((d) => ({ ...d, [pid]: slot }));
    setHistory((h) => [...h, { pick: currentPick, pid, slot, mine }]);
    if (mine) setMyRoster((r) => [...r, pid]);
    setSim(null);
  };

  const undo = () => {
    if (history.length === 0) return;
    const last = history[history.length - 1];
    setHistory((h) => h.slice(0, -1));
    setDrafted((d) => { const n = { ...d }; delete n[last.pid]; return n; });
    if (last.mine) setMyRoster((r) => r.filter((p) => p !== last.pid));
    setSim(null);
  };

  const updateManualValue = () => {
    const value = Number(manualValue);
    if (!manualPlayerId || !Number.isFinite(value) || value < -10000 || value > 10000) {
      setCustomMessage('Choose a player and enter a valid Value/VORP number.');
      return;
    }
    persistCustomSettings({
      ...customSettings,
      entries: {
        ...customSettings.entries,
        [manualPlayerId]: {
          ...customSettings.entries[manualPlayerId],
          value,
          source: 'manual',
        },
      },
    });
    setCustomMessage(`Saved ${byId[manualPlayerId]?.name || 'player'} at ${value}.`);
  };

  const toggleAvoid = (playerId: string) => {
    const existing = customSettings.entries[playerId];
    const nextAvoid = !existing?.avoid;
    const nextEntries = { ...customSettings.entries };
    const nextEntry = { ...existing, avoid: nextAvoid || undefined, source: existing?.source || 'manual' };
    if (nextEntry.value === undefined && !nextEntry.avoid) delete nextEntries[playerId];
    else nextEntries[playerId] = nextEntry;
    persistCustomSettings({ ...customSettings, entries: nextEntries });
  };

  const readCustomCsv = async (file?: File) => {
    if (!file || !players) return;
    const preview = previewCustomValuesCsv(await file.text(), players);
    setCsvPreview(preview);
    setCustomMessage(null);
  };

  const applyCsvPreview = () => {
    if (!csvPreview) return;
    const nextEntries = { ...customSettings.entries };
    let applied = 0;
    csvPreview.matches.forEach((match) => {
      if (!match.player_id || match.value === undefined || match.error) return;
      const existing = nextEntries[match.player_id];
      // A deliberate manual edit wins over a subsequent bulk upload.
      if (existing?.source === 'manual' && existing.value !== undefined) return;
      nextEntries[match.player_id] = {
        ...existing,
        value: match.value,
        source: 'upload',
      };
      applied += 1;
    });
    persistCustomSettings({ ...customSettings, entries: nextEntries });
    setCustomMessage(`Applied ${applied} uploaded values.`);
  };

  const clearCustomSettings = () => {
    persistCustomSettings(emptyCustomDraftSettings());
    setCsvPreview(null);
    setManualPlayerId('');
    setManualValue('');
    setCustomMessage('Cleared custom values and Avoid selections for this config.');
  };

  const recommend = async () => {
    if (!players) return;
    setSimLoading(true);
    try {
      const slots: Record<string, number> = {};
      Object.entries(starterSlots).forEach(([k, v]) => { if (v > 0) slots[k] = v; });
      if (superflex) slots.SUPER_FLEX = 1;
      const resp = await api.postDraftHelpSim({
        year, teams, rounds, my_slot: mySlot, ppr, superflex,
        drafted_ids: Object.keys(drafted),
        my_roster_ids: myRoster,
        slots,
        current_pick: currentPick,
        n_sims: 60, top_k: 8, seed: 1,
        value_overrides: valueOverrides,
        avoid_ids: avoidIds,
        priority_candidate_ids: priorityCandidateIds,
      });
      setSim(resp);
    } catch (e) {
      setError('Sim failed.');
    } finally {
      setSimLoading(false);
    }
  };

  return (
    <VStack align="stretch" spacing={4}>
      {/* How it works */}
      <Accordion allowToggle borderWidth="1px" borderRadius="md" bg="blue.50">
        <AccordionItem border="none">
          <AccordionButton _expanded={{ bg: 'blue.100' }} _hover={{ bg: 'blue.100' }}>
            <Box flex="1" textAlign="left" fontSize="md" fontWeight="semibold" color="blue.800">
              How does &quot;Recommend my pick&quot; work?
            </Box>
            <AccordionIcon color="blue.800" />
          </AccordionButton>
          <AccordionPanel pb={4}>
            <VStack align="stretch" spacing={3} fontSize="sm" color="gray.700">
              <Text>
                For every player you could take right now, it runs a{' '}
                <b>Monte-Carlo simulation</b> of the rest of the draft (~60 times). Your
                opponents draft by <b>real ADP</b> with realistic variance, so positions
                &quot;run&quot; just like a live draft. Each rollout it builds the best{' '}
                <b>starting lineup</b> you&apos;d finish with (scored by VBD — value over
                replacement) and then recommends the player that gives you the{' '}
                <b>best overall team</b>, not just the best player on the board now.
              </Text>
              <Box bg="white" borderWidth="1px" borderRadius="md" p={3}>
                <Text fontWeight="semibold" mb={1}>Why it sometimes passes on the highest-VBD player</Text>
                <Text color="gray.600">
                  If that player is likely to still be there at your <i>next</i> pick, it grabs a
                  scarcer one that won&apos;t be — so you bank both. Example at the 1st/2nd-round
                  turn: two elite WRs and two RBs are on the board. If ADP says the RBs will be
                  gone by your next pick but the WRs will slide, it recommends an{' '}
                  <b>RB now</b> — you still land a top WR a few picks later, ending up with both.
                </Text>
              </Box>
              <Box overflowX="auto">
                <Table size="sm" variant="simple">
                  <Thead>
                    <Tr><Th>Column</Th><Th>What it means</Th></Tr>
                  </Thead>
                  <Tbody>
                    <Tr><Td><b>VAL</b></Td><Td>Projected value of your whole starting lineup if you take this player — the number it ranks by. Higher = better team.</Td></Tr>
                    <Tr><Td><b>VBD</b></Td><Td>This player&apos;s own value over replacement (standalone).</Td></Tr>
                    <Tr><Td><b>Likely next</b></Td><Td>Who you&apos;ll most often still get at each of your upcoming picks.</Td></Tr>
                  </Tbody>
                </Table>
              </Box>
            </VStack>
          </AccordionPanel>
        </AccordionItem>
      </Accordion>

      {/* Config */}
      <Box borderWidth="1px" borderRadius="md" p={3}>
        <HStack spacing={3} flexWrap="wrap">
          <Box>
            <Text fontSize="xs" color="gray.500">Season</Text>
            <Select size="sm" value={year} onChange={(e) => setYear(e.target.value)} w="100px">
              {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
            </Select>
          </Box>
          <Box>
            <Text fontSize="xs" color="gray.500">Teams</Text>
            <Select size="sm" value={teams} onChange={(e) => setTeams(Number(e.target.value))} w="90px">
              {TEAM_SIZES.map((t) => <option key={t} value={t}>{t}</option>)}
            </Select>
          </Box>
          <Box>
            <Text fontSize="xs" color="gray.500">Rounds</Text>
            <Input size="sm" type="number" value={rounds} min={1} max={30} w="80px"
              onChange={(e) => setRounds(Math.max(1, Math.min(30, Number(e.target.value) || 1)))} />
          </Box>
          <Box>
            <Text fontSize="xs" color="gray.500">Your slot</Text>
            <Select size="sm" value={mySlot} onChange={(e) => setMySlot(Number(e.target.value))} w="90px">
              {Array.from({ length: teams }, (_, i) => i + 1).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Select>
          </Box>
          <Box>
            <Text fontSize="xs" color="gray.500">Scoring</Text>
            <Select size="sm" value={ppr} onChange={(e) => setPpr(Number(e.target.value))} w="110px">
              {PPRS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </Select>
          </Box>
          <Box alignSelf="flex-end">
            <Checkbox isChecked={superflex} onChange={(e) => setSuperflex(e.target.checked)}>
              Superflex
            </Checkbox>
          </Box>
          <Box alignSelf="flex-end">
            <Button size="sm" colorScheme="blue" onClick={loadBoard} isLoading={loading}>
              {players ? 'Reload board' : 'Load board'}
            </Button>
          </Box>
        </HStack>
        <HStack spacing={3} flexWrap="wrap" mt={3} align="flex-end">
          <Text fontSize="xs" color="gray.500" alignSelf="center">Starting slots</Text>
          {SLOT_KEYS.map(({ key, label }) => (
            <Box key={key}>
              <Text fontSize="xs" color="gray.500">{label}</Text>
              <Input
                size="sm" type="number" w="62px" min={0} max={10}
                value={starterSlots[key] ?? 0}
                onChange={(e) => setStarterSlots((s) => ({
                  ...s, [key]: Math.max(0, Math.min(10, Number(e.target.value) || 0)),
                }))}
              />
            </Box>
          ))}
          {superflex && <Badge colorScheme="purple" alignSelf="center">+ SUPERFLEX slot</Badge>}
        </HStack>
        {sources && (
          <HStack spacing={2} mt={3} flexWrap="wrap">
            <Badge colorScheme={sources.values?.source === 'custom upload required' ? 'orange' : 'blue'}>
              Values: {sources.values?.source || 'default rankings'}
            </Badge>
            <Badge colorScheme={sources.adp?.source ? 'green' : 'orange'}>
              ADP: {sources.adp?.source === 'fantasypros_draftwizard'
                ? 'FantasyPros DraftWizard'
                : sources.adp?.source === 'fantasyfootballcalculator'
                  ? 'FantasyFootballCalculator'
                  : sources.adp?.source || 'rank fallback'}
              {sources.adp?.total_drafts ? ` · ${sources.adp.total_drafts.toLocaleString()} drafts` : ''}
            </Badge>
            {sources.adp?.generated_at_utc && (
              <Text fontSize="xs" color="gray.500">
                refreshed {new Date(sources.adp.generated_at_utc).toLocaleDateString()}
              </Text>
            )}
          </HStack>
        )}
      </Box>

      {players && (
        <Accordion allowToggle borderWidth="1px" borderRadius="md">
          <AccordionItem border="none">
            <AccordionButton _expanded={{ bg: 'gray.50' }}>
              <Box flex="1" textAlign="left" color="gray.800">
                <Text fontWeight="semibold" color="gray.800">Custom values &amp; preferences</Text>
                <Text fontSize="xs" color="gray.500">
                  {Object.values(customSettings.entries).filter((entry) => entry.value !== undefined).length} custom values
                  {' · '}{avoidIds.length} avoided · saved in this browser for this config
                </Text>
              </Box>
              <AccordionIcon />
            </AccordionButton>
            <AccordionPanel>
              <VStack align="stretch" spacing={4}>
                <Text fontSize="sm" color="gray.600">
                  Values must be cross-position VBD/VORP numbers—not raw fantasy points.
                  They change your board and recommendations, but ADP still controls when
                  opponents take players. CSV headers: <b>player_id</b> or <b>name</b>,
                  optional <b>position</b>, and <b>value</b>/<b>VBD</b>/<b>VORP</b>.
                </Text>

                <Box>
                  <Text fontSize="sm" fontWeight="semibold" mb={1}>Upload CSV</Text>
                  <Input
                    size="sm"
                    type="file"
                    accept=".csv,text/csv"
                    p={1}
                    onChange={(event) => readCustomCsv(event.target.files?.[0])}
                  />
                  {csvPreview && (
                    <Box mt={2} borderWidth="1px" borderRadius="md" p={2}>
                      <HStack spacing={2} flexWrap="wrap">
                        <Badge colorScheme="green">
                          {csvPreview.matches.filter((match) => match.player_id && !match.error).length} matched
                        </Badge>
                        <Badge colorScheme={csvPreview.matches.some((match) => match.error) ? 'orange' : 'gray'}>
                          {csvPreview.matches.filter((match) => match.error).length} skipped
                        </Badge>
                        <Button
                          size="xs"
                          colorScheme="blue"
                          onClick={applyCsvPreview}
                          isDisabled={!!csvPreview.errors.length || !csvPreview.matches.some((match) => match.player_id && !match.error)}
                        >
                          Apply matched values
                        </Button>
                      </HStack>
                      {csvPreview.errors.map((message) => (
                        <Text key={message} fontSize="xs" color="red.600" mt={1}>{message}</Text>
                      ))}
                      {csvPreview.matches.filter((match) => match.error).slice(0, 8).map((match) => (
                        <Text key={`${match.row}-${match.input_name}`} fontSize="xs" color="orange.700" mt={1}>
                          Row {match.row}: {match.input_name || match.input_player_id || '(blank)'} — {match.error}
                        </Text>
                      ))}
                    </Box>
                  )}
                </Box>

                <Box>
                  <Text fontSize="sm" fontWeight="semibold" mb={1}>Manual value</Text>
                  <HStack align="flex-end" flexWrap="wrap">
                    <PlayerCombobox
                      players={effectivePlayers}
                      value={manualPlayerId}
                      onChange={(pid) => {
                        setManualPlayerId(pid);
                        const current = customSettings.entries[pid]?.value;
                        setManualValue(current === undefined ? '' : String(current));
                      }}
                    />
                    <Input
                      size="sm"
                      type="number"
                      value={manualValue}
                      placeholder="Value/VORP"
                      onChange={(event) => setManualValue(event.target.value)}
                      w="130px"
                    />
                    <Button size="sm" colorScheme="blue" onClick={updateManualValue}>
                      Save value
                    </Button>
                    <Button size="sm" variant="outline" colorScheme="red" onClick={clearCustomSettings}>
                      Clear all
                    </Button>
                  </HStack>
                </Box>
                {customMessage && <Text fontSize="xs" color="blue.700">{customMessage}</Text>}
              </VStack>
            </AccordionPanel>
          </AccordionItem>
        </Accordion>
      )}

      {players && isAdpOnly && (
        <Box borderWidth="1px" borderColor="orange.300" bg="orange.50" borderRadius="md" p={3}>
          <Text fontSize="sm" color="orange.800">
            This is a current ADP-only board; no old projections were carried
            forward into 2026. Add at least {MIN_ADP_ONLY_VALUES} Value/VORP rows
            before requesting a recommendation ({usableValueCount} currently loaded).
            A complete sheet is strongly recommended.
          </Text>
        </Box>
      )}

      {error && <Text color="red.500">{error}</Text>}

      {players && (
        <>
          <HStack justify="space-between" flexWrap="wrap">
            <HStack>
              <Badge colorScheme={isMyPick ? 'green' : 'gray'} fontSize="sm">
                {draftOver ? 'Draft complete' : `Pick ${currentPick} • Team ${onClock} on the clock`}
              </Badge>
              {isMyPick && !draftOver && <Badge colorScheme="green">YOUR PICK</Badge>}
            </HStack>
            <HStack>
              <Button size="sm" onClick={undo} isDisabled={history.length === 0}>Undo</Button>
              <Tooltip
                label="Monte-Carlo: simulates the rest of the draft from real ADP and recommends the player that builds your best starting lineup — not just the highest VBD."
                hasArrow placement="top" openDelay={250} shouldWrapChildren>
                <Button size="sm" colorScheme="green" onClick={recommend}
                  isDisabled={draftOver || !hasUsableValues} isLoading={simLoading}>
                  Recommend my pick
                </Button>
              </Tooltip>
            </HStack>
          </HStack>

          {sim && sim.recommendation && (
            <Box borderWidth="1px" borderColor="green.300" borderRadius="md" p={3} bg="green.50">
              <Heading size="sm" mb={1}>
                Recommended: {sim.recommendation.name} ({sim.recommendation.pos})
              </Heading>
              {confidence && (
                <HStack mb={2}>
                  <Badge colorScheme={confidence.color}>{confidence.label}</Badge>
                  <Text fontSize="xs" color="gray.600">{confidence.detail}</Text>
                  {sim.cache_hit && <Badge colorScheme="gray">cached state</Badge>}
                </HStack>
              )}
              <Text fontSize="xs" color="gray.600" mb={2}>
                VAL = your projected <b>starting lineup</b> total value over replacement (VBD),
                with every required slot filled across all positions — so it balances positions
                and won&apos;t over-draft a shallow one (e.g. QB in 1-QB); depth only breaks
                ties. VBD = this player&apos;s value over replacement. Likely next = the player
                you most often take at each of your next pick slots (P# = pick number). The
                smaller range below VAL is the middle 50% of simulated lineup outcomes.
              </Text>
              <Box overflowX="auto">
                <Table size="sm" variant="simple">
                  <Thead>
                    <Tr><Th>Player</Th><Th>Pos</Th><Th isNumeric>ADP</Th><Th isNumeric>VBD</Th>
                      <Th isNumeric title="Middle 50% rollout range is shown below the mean">VAL</Th><Th>Likely next picks</Th></Tr>
                  </Thead>
                  <Tbody>
                    {sim.candidates.map((c, i) => (
                      <Tr key={c.player_id} bg={i === 0 ? 'green.100' : undefined}>
                        <Td><HStack spacing={2}><DraftPlayerAvatar playerId={c.player_id} name={c.name} team={byId[c.player_id]?.team} size={30} /><Text>{c.name}</Text></HStack></Td>
                        <Td>{c.pos}</Td>
                        <Td isNumeric>{Math.round(c.adp)}</Td>
                        <Td isNumeric>{c.proj.toFixed(0)}</Td>
                        <Td isNumeric fontWeight={i === 0 ? 'bold' : 'normal'}>
                          {c.avg_lineup.toFixed(1)}
                          {c.lineup_p25 != null && c.lineup_p75 != null && (
                            <Text fontSize="2xs" color="gray.500">{c.lineup_p25.toFixed(0)}–{c.lineup_p75.toFixed(0)}</Text>
                          )}
                        </Td>
                        <Td fontSize="xs" color="gray.700">
                          {c.likely_next && c.likely_next.length > 0
                            ? c.likely_next
                                .map((lp) => `P${lp.pick_no}: ${lp.name} (${lp.pos} ${Math.round(lp.pct * 100)}%)`)
                                .join(' · ')
                            : '—'}
                        </Td>
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              </Box>
              {sim.priority_candidates && sim.priority_candidates.some(
                (target) => !sim.candidates.some((candidate) => candidate.player_id === target.player_id),
              ) && (
                <Box mt={3} pt={2} borderTopWidth="1px">
                  <Text fontSize="xs" fontWeight="semibold" color="blue.800">
                    Manually adjusted targets evaluated outside the top five
                  </Text>
                  {sim.priority_candidates
                    .filter((target) => !sim.candidates.some(
                      (candidate) => candidate.player_id === target.player_id,
                    ))
                    .map((target) => (
                      <Text key={target.player_id} fontSize="xs" color="gray.600">
                        {target.name} ({target.pos}) — VAL {target.avg_lineup.toFixed(1)}.
                        The model preferred another player now, often because this target
                        is likely to remain available later.
                      </Text>
                    ))}
                </Box>
              )}
            </Box>
          )}

          <SimpleGrid columns={{ base: 1, lg: 3 }} spacing={4}>
            {/* Available board */}
            <Box gridColumn={{ lg: 'span 2' }}>
              <HStack mb={2}>
                <Text fontWeight="semibold">Available</Text>
                <Select size="xs" w="110px" value={posFilter} onChange={(e) => setPosFilter(e.target.value)}>
                  {['ALL', 'QB', 'RB', 'WR', 'TE'].map((p) => <option key={p} value={p}>{p}</option>)}
                </Select>
              </HStack>
              <Box overflowX="auto" maxH="520px" overflowY="auto" borderWidth="1px" borderRadius="md">
                <Table size="sm" variant="simple">
                  <Thead position="sticky" top={0} bg="white" zIndex={1}>
                    <Tr>
                      <Th
                        isNumeric cursor="pointer" userSelect="none"
                        color={boardSort === 'adp' ? 'blue.600' : undefined}
                        onClick={() => setBoardSort('adp')}
                        title="Average draft position — sort to draft opponents like a real draft"
                      >
                        ADP{boardSort === 'adp' ? ' ↓' : ''}
                      </Th>
                      <Th
                        isNumeric cursor="pointer" userSelect="none"
                        color={boardSort === 'vbd' ? 'blue.600' : undefined}
                        onClick={() => setBoardSort('vbd')}
                        title="Value over replacement — sort to see the best value still available"
                      >
                        VBD{boardSort === 'vbd' ? ' ↓' : ''}
                      </Th>
                      <Th>Player</Th><Th>Pos</Th><Th>Tier</Th>
                      <Th isNumeric>Proj</Th><Th isNumeric>$</Th><Th></Th>
                    </Tr>
                  </Thead>
                  <Tbody>
                    {available.map((p) => {
                      const custom = customSettings.entries[p.player_id];
                      return (
                      <Tr key={p.player_id} bg={custom?.avoid ? 'red.50' : undefined}>
                        <Td isNumeric>{p.adp != null ? Math.round(p.adp) : '—'}</Td>
                        <Td isNumeric color={custom?.value !== undefined ? 'blue.600' : undefined} fontWeight={custom?.value !== undefined ? 'bold' : undefined}>
                          {p.vbd != null ? Math.round(p.vbd) : ''}
                        </Td>
                        <Td>
                          <HStack spacing={2}>
                            <DraftPlayerAvatar playerId={p.player_id} name={p.name} team={p.team} />
                            <Box>
                              {p.name}
                              {custom?.value !== undefined && <Badge ml={1} colorScheme="blue">custom</Badge>}
                              {custom?.avoid && <Badge ml={1} colorScheme="red">avoid</Badge>}
                            </Box>
                          </HStack>
                        </Td>
                        <Td>{p.pos}</Td>
                        <Td>{p.tier ?? ''}</Td>
                        <Td isNumeric>{p.fpts != null ? p.fpts.toFixed(0) : ''}</Td>
                        <Td isNumeric>{p.auction != null ? `$${p.auction.toFixed(0)}` : ''}</Td>
                        <Td>
                          <HStack spacing={1}>
                            <Button
                              size="xs"
                              variant={custom?.avoid ? 'solid' : 'outline'}
                              colorScheme="red"
                              onClick={() => toggleAvoid(p.player_id)}
                            >
                              {custom?.avoid ? 'Allow' : 'Avoid'}
                            </Button>
                            <Button size="xs" colorScheme={isMyPick ? 'green' : 'gray'}
                              onClick={() => draftPlayer(p.player_id)} isDisabled={draftOver}>
                              Draft
                            </Button>
                          </HStack>
                        </Td>
                      </Tr>
                    );})}
                  </Tbody>
                </Table>
              </Box>
            </Box>

            {/* My roster */}
            <Box>
              <Text fontWeight="semibold" mb={2}>My roster ({myRoster.length}/{rounds})</Text>
              <VStack align="stretch" spacing={1} borderWidth="1px" borderRadius="md" p={2} maxH="520px" overflowY="auto">
                {rosterSlots.map((s, i) => {
                  const color = s.player ? (POS_COLOR[s.player.pos] || 'gray') : (SLOT_COLOR[s.type] || 'gray');
                  return (
                    <HStack
                      key={`${s.type}-${i}`}
                      justify="space-between"
                      spacing={2}
                      px={2}
                      py={1}
                      borderRadius="sm"
                      borderLeftWidth="4px"
                      borderLeftColor={`${color}.400`}
                      bg={s.player ? `${color}.50` : 'gray.50'}
                    >
                      <HStack spacing={2} minW={0}>
                        <Badge minW="34px" textAlign="center" colorScheme={SLOT_COLOR[s.type] || 'gray'}>
                          {s.label}
                        </Badge>
                        {s.player && <DraftPlayerAvatar playerId={s.player.player_id} name={s.player.name} team={s.player.team} size={28} />}
                        <Text fontSize="sm" noOfLines={1} color={s.player ? undefined : 'gray.400'}>
                          {s.player ? s.player.name : 'Empty'}
                        </Text>
                      </HStack>
                      {s.player && <Badge colorScheme={POS_COLOR[s.player.pos] || 'gray'}>{s.player.pos}</Badge>}
                    </HStack>
                  );
                })}
              </VStack>
            </Box>
          </SimpleGrid>
        </>
      )}

      {simLoading && <HStack><Spinner size="sm" /><Text>Simulating…</Text></HStack>}
    </VStack>
  );
};

export default MockDraftView;
