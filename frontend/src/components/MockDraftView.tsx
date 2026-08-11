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
  Alert,
  AlertIcon,
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
  Textarea,
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
  previewElBobertoValues,
  saveCustomDraftSettings,
} from '../utils/customDraftValues';
import {
  buildRosterSlots,
  POS_COLOR,
  SLOT_COLOR,
} from '../utils/draftRoster';
import { confidencePresentation } from '../utils/simConfidence';
import {
  centralizedProfileId,
  derivedRounds,
  normalizedStarterSlots,
  profileStorageSignature,
  providerProfileMatches,
} from '../utils/draftValueProfile';

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
const SLOT_OPTIONS: Record<string, number[]> = {
  QB: [1, 2],
  RB: [1, 2, 3],
  WR: [1, 2, 3],
  TE: [0, 1, 2, 3],
  FLEX: [0, 1, 2, 3],
};

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
  const [benchSize, setBenchSize] = useState(6);
  const [passingTd, setPassingTd] = useState(4);
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
  const [elBobertoPaste, setElBobertoPaste] = useState('');
  const [elBobertoPreview, setElBobertoPreview] = useState<CustomCsvPreview | null>(null);
  const [manualPlayerId, setManualPlayerId] = useState('');
  const [manualValue, setManualValue] = useState('');
  const [customMessage, setCustomMessage] = useState<string | null>(null);

  // Sim
  const [sim, setSim] = useState<SimResponse | null>(null);
  const [simLoading, setSimLoading] = useState(false);

  const rounds = derivedRounds(starterSlots, superflex, benchSize);

  const currentPick = history.length + 1;
  const totalPicks = teams * rounds;
  const onClock = snakeSlot(currentPick, teams);
  const isMyPick = onClock === mySlot;
  const draftOver = currentPick > totalPicks;

  const profileSignature = useMemo(
    () => profileStorageSignature(starterSlots, superflex, benchSize, passingTd),
    [starterSlots, superflex, benchSize, passingTd],
  );
  const storageKey = useMemo(
    () => customDraftStorageKey(year, teams, ppr, superflex, profileSignature),
    [year, teams, ppr, superflex, profileSignature],
  );

  useEffect(() => {
    const loaded = typeof window === 'undefined'
      ? emptyCustomDraftSettings()
      : loadCustomDraftSettings(window.localStorage, storageKey);
    setCustomSettings(loaded);
    setElBobertoPaste('');
    setElBobertoPreview(null);
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

  const profileMatches = providerProfileMatches(
    sources?.values?.profile, starterSlots, benchSize, passingTd,
  );
  const requestedProfileId = centralizedProfileId(
    starterSlots, superflex, benchSize, passingTd,
  );
  const useProviderValues = !sources ? true : profileMatches;
  const effectivePlayers = useMemo(() => (players || []).map((player) => {
    const custom = customSettings.entries[player.player_id];
    if (custom?.value !== undefined) return { ...player, vbd: custom.value };
    if (useProviderValues) return player;
    return { ...player, vbd: null, fpts: null, auction: null, tier: null };
  }), [players, customSettings, useProviderValues]);

  const valueOverrides = useMemo(
    () => customValueMap(customSettings),
    [customSettings],
  );
  const importedOverrideCount = useMemo(
    () => Object.values(customSettings.entries).filter(
      (entry) => entry.value !== undefined && entry.source !== 'manual',
    ).length,
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
  const customProfileRequired = isAdpOnly || !useProviderValues;
  const hasUsableValues = customProfileRequired
    ? usableValueCount >= MIN_ADP_ONLY_VALUES
    : usableValueCount > 0;

  const byId = useMemo(() => {
    const m: Record<string, RankingsPlayerRow> = {};
    effectivePlayers.forEach((p) => { m[p.player_id] = p; });
    return m;
  }, [effectivePlayers]);
  const providerById = useMemo(() => Object.fromEntries(
    (players || []).map((player) => [player.player_id, player]),
  ), [players]);

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
      normalizedStarterSlots(starterSlots, superflex),
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
      const resp = await api.getDraftHelpRankings(
        year, teams, ppr, superflex, requestedProfileId,
      );
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

  const previewElBobertoPaste = () => {
    if (!players) return;
    setElBobertoPreview(
      previewElBobertoValues(elBobertoPaste, players),
    );
    setCustomMessage(null);
  };

  const applyElBobertoPreview = () => {
    if (!elBobertoPreview) return;
    const nextEntries = { ...customSettings.entries };
    let applied = 0;
    elBobertoPreview.matches.forEach((match) => {
      if (!match.player_id || match.value === undefined || match.error) return;
      const existing = nextEntries[match.player_id];
      if (existing?.source === 'manual' && existing.value !== undefined) return;
      nextEntries[match.player_id] = {
        ...existing,
        value: match.value,
        source: 'elboberto_paste',
      };
      applied += 1;
    });
    persistCustomSettings({ ...customSettings, entries: nextEntries });
    setCustomMessage(`Applied ${applied} ElBoberto values for this custom profile.`);
  };

  const clearCustomSettings = () => {
    persistCustomSettings(emptyCustomDraftSettings());
    setElBobertoPaste('');
    setElBobertoPreview(null);
    setManualPlayerId('');
    setManualValue('');
    setCustomMessage('Cleared custom values and Avoid selections for this config.');
  };

  const clearImportedValues = () => {
    const entries: CustomDraftSettings['entries'] = {};
    Object.entries(customSettings.entries).forEach(([playerId, entry]) => {
      if (entry.source === 'manual') {
        entries[playerId] = entry;
      } else if (entry.avoid) {
        entries[playerId] = { avoid: true, source: 'manual' };
      }
    });
    persistCustomSettings({ ...customSettings, entries });
    setElBobertoPreview(null);
    setCustomMessage('Removed imported values; provider AvgVBD is active again. Manual edits and Avoid selections were preserved.');
  };

  const recommend = async () => {
    if (!players) return;
    setSimLoading(true);
    try {
      const slots = normalizedStarterSlots(starterSlots, superflex);
      const resp = await api.postDraftHelpSim({
        year, teams, rounds, my_slot: mySlot, ppr, superflex,
        drafted_ids: Object.keys(drafted),
        my_roster_ids: myRoster,
        slots,
        current_pick: currentPick,
        n_sims: 60, top_k: 8, seed: 1,
        value_overrides: valueOverrides,
        use_provider_values: useProviderValues,
        profile_id: requestedProfileId || undefined,
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
                <b>starting lineup</b> you&apos;d finish with plus a small, geometrically
                discounted bench-depth bonus (scored by VBD — value over replacement),
                then recommends the player that gives you the{' '}
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
                    <Tr><Td><b>VAL</b></Td><Td>Projected value of your full starting lineup plus discounted bench depth if you take this player — the number it ranks by. Higher = better team.</Td></Tr>
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
            <Text fontSize="xs" color="gray.500">Bench</Text>
            <Select size="sm" value={benchSize} onChange={(e) => setBenchSize(Number(e.target.value))} w="80px">
              {[3, 4, 5, 6, 7, 8].map((count) => <option key={count} value={count}>{count}</option>)}
            </Select>
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
          <Box>
            <Text fontSize="xs" color="gray.500">Pass TD</Text>
            <Select size="sm" value={passingTd} onChange={(e) => setPassingTd(Number(e.target.value))} w="85px">
              <option value={4}>4 pt</option>
              <option value={6}>6 pt</option>
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
              <Select
                size="sm" w="62px"
                value={starterSlots[key] ?? 0}
                onChange={(e) => setStarterSlots((s) => ({
                  ...s, [key]: Number(e.target.value),
                }))}
              >
                {SLOT_OPTIONS[key].map((count) => (
                  <option key={count} value={count}>{count}</option>
                ))}
              </Select>
            </Box>
          ))}
          {superflex && <Badge colorScheme="purple" alignSelf="center">+ SUPERFLEX slot</Badge>}
          <Badge colorScheme="gray" alignSelf="center">{rounds} rounds incl. K/DEF</Badge>
        </HStack>
        {sources && (
          <HStack spacing={2} mt={3} flexWrap="wrap">
            <Badge colorScheme={sources.values?.source === 'custom upload required' ? 'orange' : 'blue'}>
              Values: {sources.values?.source || 'default rankings'}
              {sources.values?.source_version ? ` v${sources.values.source_version}` : ''}
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
            {(sources.values?.retrieved_at_utc || sources.values?.generated_at_utc) && (
              <Text fontSize="xs" color="gray.500">
                values refreshed {new Date(
                  sources.values.retrieved_at_utc || sources.values.generated_at_utc || '',
                ).toLocaleDateString()}
              </Text>
            )}
          </HStack>
        )}
        {players && sources?.values?.provider && !useProviderValues && (
          <Alert status="warning" mt={3} borderRadius="md" alignItems="flex-start">
            <AlertIcon />
            <Box flex="1" fontSize="sm">
              This exact profile is not in the published {sources.values.source} blob. Provider
              VBD is disabled rather than pretending the default sheet fits. Paste at least
              {` ${MIN_ADP_ONLY_VALUES} `}finished ElBoberto values configured for these settings
              ({usableValueCount} loaded), or reset to 1 QB / 2 RB / 2 WR / 1 TE / 1 FLEX,
              6 bench, and 4-point passing TD.
            </Box>
            <Button size="xs" ml={2} onClick={() => {
              setStarterSlots({ QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1 });
              setBenchSize(6);
              setPassingTd(4);
            }}>
              Published profile
            </Button>
          </Alert>
        )}
        {sources?.values?.provider && useProviderValues && importedOverrideCount > 0 && (
          <Alert status="warning" mt={3} borderRadius="md">
            <AlertIcon />
            <Box flex="1" fontSize="sm">
              {importedOverrideCount} saved bulk values currently override {sources.values.source || 'the provider'} AvgVBD.
              The board shows both columns below so the source values are never hidden.
            </Box>
            <Button size="xs" ml={2} onClick={clearImportedValues}>
              Use provider values
            </Button>
          </Alert>
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
                  opponents take players. Manual edits always override a pasted sheet.
                </Text>

                <Box>
                  <Text fontSize="sm" fontWeight="semibold" mb={1}>
                    Paste custom ElBoberto values
                  </Text>
                  <Text fontSize="xs" color="gray.600" mb={2}>
                    In the ElBoberto workbook, configure your league, open <b>CheatSheet</b>,
                    copy the used table (or just <b>OVR / Player / Pos / VBD</b>), and paste
                    it here. These values are saved only for this exact browser profile.
                  </Text>
                  <Textarea
                    size="sm"
                    value={elBobertoPaste}
                    onChange={(event) => {
                      setElBobertoPaste(event.target.value);
                      setElBobertoPreview(null);
                    }}
                    placeholder={'OVR\tPlayer\tPos\tVBD\n1\tJahmyr Gibbs\tRB\t211.01'}
                    minH="120px"
                  />
                  <Button
                    size="xs"
                    mt={2}
                    onClick={previewElBobertoPaste}
                    isDisabled={!elBobertoPaste.trim()}
                  >
                    Preview ElBoberto values
                  </Button>
                  {elBobertoPreview && (
                    <Box mt={2} borderWidth="1px" borderRadius="md" p={2}>
                      <HStack spacing={2} flexWrap="wrap">
                        <Badge colorScheme="green">
                          {elBobertoPreview.matches.filter((match) => match.player_id && !match.error).length} matched
                        </Badge>
                        <Badge colorScheme={elBobertoPreview.matches.some((match) => match.error) ? 'orange' : 'gray'}>
                          {elBobertoPreview.matches.filter((match) => match.error).length} skipped
                        </Badge>
                        <Button
                          size="xs"
                          colorScheme="blue"
                          onClick={applyElBobertoPreview}
                          isDisabled={!!elBobertoPreview.errors.length || !elBobertoPreview.matches.some((match) => match.player_id && !match.error)}
                        >
                          Apply matched values
                        </Button>
                      </HStack>
                      {elBobertoPreview.errors.map((message) => (
                        <Text key={message} fontSize="xs" color="red.600" mt={1}>{message}</Text>
                      ))}
                      {elBobertoPreview.matches.filter((match) => match.error).slice(0, 8).map((match) => (
                        <Text key={`${match.row}-${match.input_name}`} fontSize="xs" color="orange.700" mt={1}>
                          Row {match.row}: {match.input_name || '(blank)'} — {match.error}
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

      {players && customProfileRequired && (
        <Box borderWidth="1px" borderColor="orange.300" bg="orange.50" borderRadius="md" p={3}>
          <Text fontSize="sm" color="orange.800">
            Provider values are unavailable for this exact profile. Paste at least{' '}
            {MIN_ADP_ONLY_VALUES} finished ElBoberto Value/VORP rows before requesting
            a recommendation ({usableValueCount} currently loaded). A complete sheet
            is strongly recommended.
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
                label="Monte-Carlo: simulates the rest of the draft from real ADP and recommends the player that builds your best starters plus discounted depth — not just the highest VBD."
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
                VAL = your projected <b>starting lineup</b> total value over replacement (VBD)
                plus heavily discounted depth. Starters count fully; the best backup at each
                startable position counts 10%, the next 1%, and the third 0.1%. That keeps
                starters dominant while still recognizing an extreme bench bargain. VBD = this
                player&apos;s value over replacement. Likely next = the player
                you most often take at each of your next pick slots (P# = pick number). The
                smaller range below VAL is the middle 50% of simulated lineup outcomes.
              </Text>
              <Box overflowX="auto">
                <Table size="sm" variant="simple">
                  <Thead>
                    <Tr><Th>Player</Th><Th>Pos</Th><Th isNumeric>ADP</Th><Th isNumeric>Used VBD</Th>
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
                          {c.avg_value.toFixed(1)}
                          {c.value_p25 != null && c.value_p75 != null && (
                            <Text fontSize="2xs" color="gray.500">{c.value_p25.toFixed(0)}–{c.value_p75.toFixed(0)}</Text>
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
                        {target.name} ({target.pos}) — VAL {target.avg_value.toFixed(1)}.
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
                      <Th isNumeric title="Finished AvgVBD supplied by the selected provider">
                        Source VBD
                      </Th>
                      <Th
                        isNumeric cursor="pointer" userSelect="none"
                        color={boardSort === 'vbd' ? 'blue.600' : undefined}
                        onClick={() => setBoardSort('vbd')}
                        title="Value over replacement — sort to see the best value still available"
                      >
                        Used VBD{boardSort === 'vbd' ? ' ↓' : ''}
                      </Th>
                      <Th>Player</Th><Th>Pos</Th><Th>Tier</Th>
                      <Th isNumeric>Proj</Th><Th isNumeric>$</Th><Th></Th>
                    </Tr>
                  </Thead>
                  <Tbody>
                    {available.map((p) => {
                      const custom = customSettings.entries[p.player_id];
                      const provider = providerById[p.player_id];
                      return (
                      <Tr key={p.player_id} bg={custom?.avoid ? 'red.50' : undefined}>
                        <Td isNumeric>{p.adp != null ? Math.round(p.adp) : '—'}</Td>
                        <Td isNumeric>{provider?.vbd != null ? Math.round(provider.vbd) : '—'}</Td>
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
