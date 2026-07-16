/**
 * Mock snake-draft board + Monte-Carlo pick recommender.
 *
 * Load a value board for a league config, then click players to draft them to
 * whichever team is on the clock (snake order is tracked automatically). When
 * it's your pick, "Recommend my pick" runs the backend Monte-Carlo sim
 * (/draft-help/sim) — it predicts who'll be available at your later picks via
 * ADP and scores the starting lineup you'd end up with for each candidate.
 */
import React, { useMemo, useState } from 'react';
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
import { RankingsPlayerRow, SimResponse } from '../types/draft';

const YEARS = ['2024', '2023', '2022', '2025'];
const TEAM_SIZES = [8, 10, 12, 14];
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

// Roster-slot rendering: flex eligibility, colors, and slot assignment.
const FLEX_ELIGIBLE: Record<string, string[]> = {
  FLEX: ['RB', 'WR', 'TE'],
  SUPER_FLEX: ['QB', 'RB', 'WR', 'TE'],
};
const POS_COLOR: Record<string, string> = { QB: 'purple', RB: 'green', WR: 'blue', TE: 'orange' };
const SLOT_COLOR: Record<string, string> = {
  QB: 'purple', RB: 'green', WR: 'blue', TE: 'orange',
  FLEX: 'teal', SUPER_FLEX: 'pink', BN: 'gray',
};

interface RosterSlot { type: string; label: string; player?: RankingsPlayerRow; }

/**
 * Lay out a full roster (starters in lineup order, then bench) and slot each
 * drafted player into their best-fit spot the way a real draft board would:
 * dedicated position first, then FLEX, then SUPER_FLEX, leftovers to the bench.
 */
function buildRosterSlots(
  myRoster: string[],
  byId: Record<string, RankingsPlayerRow>,
  starterSlots: Record<string, number>,
  superflex: boolean,
  rounds: number,
): RosterSlot[] {
  const byPos: Record<string, RankingsPlayerRow[]> = {};
  myRoster.forEach((pid) => {
    const p = byId[pid];
    if (!p) return;
    if (!byPos[p.pos]) byPos[p.pos] = [];
    byPos[p.pos].push(p);
  });
  Object.values(byPos).forEach((arr) =>
    arr.sort((a, b) => (a.overall_rank ?? 9999) - (b.overall_rank ?? 9999)));

  const used = new Set<string>();
  const take = (positions: string[]): RankingsPlayerRow | undefined => {
    let best: RankingsPlayerRow | undefined;
    for (const pos of positions) {
      const cand = (byPos[pos] || []).find((p) => !used.has(p.player_id));
      if (cand && (!best || (cand.overall_rank ?? 9999) < (best.overall_rank ?? 9999))) {
        best = cand;
      }
    }
    if (best) used.add(best.player_id);
    return best;
  };

  const order: Array<{ type: string; count: number }> = [
    { type: 'QB', count: starterSlots.QB ?? 0 },
    { type: 'RB', count: starterSlots.RB ?? 0 },
    { type: 'WR', count: starterSlots.WR ?? 0 },
    { type: 'TE', count: starterSlots.TE ?? 0 },
    { type: 'FLEX', count: starterSlots.FLEX ?? 0 },
    { type: 'SUPER_FLEX', count: superflex ? 1 : 0 },
  ];
  const slots: RosterSlot[] = [];
  let starterCount = 0;
  order.forEach(({ type, count }) => {
    for (let i = 0; i < count; i += 1) {
      starterCount += 1;
      const label = type === 'SUPER_FLEX' ? 'SF' : type;
      slots.push({ type, label, player: take(FLEX_ELIGIBLE[type] ?? [type]) });
    }
  });

  // Bench: drafted-but-unslotted players (draft order), then empty BN spots.
  const leftovers = myRoster
    .map((pid) => byId[pid])
    .filter((p): p is RankingsPlayerRow => !!p && !used.has(p.player_id));
  const benchCount = Math.max(rounds - starterCount, leftovers.length);
  for (let i = 0; i < benchCount; i += 1) {
    slots.push({ type: 'BN', label: 'BN', player: leftovers[i] });
  }
  return slots;
}

function snakeSlot(pick: number, teams: number): number {
  const rnd = Math.floor((pick - 1) / teams);
  const idx = (pick - 1) % teams;
  return rnd % 2 === 0 ? idx + 1 : teams - idx;
}

interface HistoryEntry { pick: number; pid: string; slot: number; mine: boolean; }

const MockDraftView: React.FC = () => {
  // Config
  const [year, setYear] = useState('2024');
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafted, setDrafted] = useState<Record<string, number>>({}); // pid -> slot
  const [myRoster, setMyRoster] = useState<string[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [posFilter, setPosFilter] = useState('ALL');
  const [boardSort, setBoardSort] = useState<'adp' | 'vbd'>('adp');

  // Sim
  const [sim, setSim] = useState<SimResponse | null>(null);
  const [simLoading, setSimLoading] = useState(false);

  const currentPick = history.length + 1;
  const totalPicks = teams * rounds;
  const onClock = snakeSlot(currentPick, teams);
  const isMyPick = onClock === mySlot;
  const draftOver = currentPick > totalPicks;

  const byId = useMemo(() => {
    const m: Record<string, RankingsPlayerRow> = {};
    (players || []).forEach((p) => { m[p.player_id] = p; });
    return m;
  }, [players]);

  const available = useMemo(() => {
    const list = (players || [])
      .filter((p) => drafted[p.player_id] === undefined)
      .filter((p) => posFilter === 'ALL' || p.pos === posFilter);
    // ADP sort = draft opponents realistically; VBD sort (via overall_rank,
    // which is assigned VBD-desc) = see the best value still on the board.
    list.sort(boardSort === 'adp'
      ? (a, b) => (a.adp ?? 9999) - (b.adp ?? 9999)
      : (a, b) => (a.overall_rank ?? 9999) - (b.overall_rank ?? 9999));
    return list.slice(0, 180);
  }, [players, drafted, posFilter, boardSort]);

  const rosterSlots = useMemo(
    () => buildRosterSlots(myRoster, byId, starterSlots, superflex, rounds),
    [myRoster, byId, starterSlots, superflex, rounds],
  );

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
      </Box>

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
                  isDisabled={draftOver} isLoading={simLoading}>
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
              <Text fontSize="xs" color="gray.600" mb={2}>
                VAL = your projected <b>starting lineup</b> total value over replacement (VBD),
                with every required slot filled across all positions — so it balances positions
                and won&apos;t over-draft a shallow one (e.g. QB in 1-QB); depth only breaks
                ties. VBD = this player&apos;s value over replacement. Likely next = the player
                you most often take at each of your next pick slots (P# = pick number).
              </Text>
              <Box overflowX="auto">
                <Table size="sm" variant="simple">
                  <Thead>
                    <Tr><Th>Player</Th><Th>Pos</Th><Th isNumeric>ADP</Th><Th isNumeric>VBD</Th>
                      <Th isNumeric>VAL</Th><Th>Likely next picks</Th></Tr>
                  </Thead>
                  <Tbody>
                    {sim.candidates.map((c, i) => (
                      <Tr key={c.player_id} bg={i === 0 ? 'green.100' : undefined}>
                        <Td>{c.name}</Td>
                        <Td>{c.pos}</Td>
                        <Td isNumeric>{Math.round(c.adp)}</Td>
                        <Td isNumeric>{c.proj.toFixed(0)}</Td>
                        <Td isNumeric fontWeight={i === 0 ? 'bold' : 'normal'}>{c.avg_lineup.toFixed(1)}</Td>
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
                    {available.map((p) => (
                      <Tr key={p.player_id}>
                        <Td isNumeric>{p.adp != null ? Math.round(p.adp) : '—'}</Td>
                        <Td isNumeric>{p.vbd != null ? Math.round(p.vbd) : ''}</Td>
                        <Td>{p.name}</Td>
                        <Td>{p.pos}</Td>
                        <Td>{p.tier ?? ''}</Td>
                        <Td isNumeric>{p.fpts != null ? p.fpts.toFixed(0) : ''}</Td>
                        <Td isNumeric>{p.auction != null ? `$${p.auction.toFixed(0)}` : ''}</Td>
                        <Td>
                          <Button size="xs" colorScheme={isMyPick ? 'green' : 'gray'}
                            onClick={() => draftPlayer(p.player_id)} isDisabled={draftOver}>
                            Draft
                          </Button>
                        </Td>
                      </Tr>
                    ))}
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
