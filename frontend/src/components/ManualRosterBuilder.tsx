import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  AlertDescription,
  AlertIcon,
  Badge,
  Box,
  Button,
  ButtonGroup,
  Checkbox,
  Divider,
  FormControl,
  FormLabel,
  HStack,
  Input,
  Select,
  Spinner,
  Text,
  Textarea,
  VStack,
  Wrap,
  WrapItem,
} from '@chakra-ui/react';

import { api } from '../api/client';
import { useUUID } from '../context/UUIDContext';
import {
  LeagueDataResponse,
  ManualCatalogPlayer,
  ManualRoster,
  ManualRosterLimits,
  ManualRosterPlayer,
  ManualRosterSlot,
} from '../types/player';
import {
  createManualRoster,
  duplicateManualRoster,
  emptyManualRosterStore,
  exportManualRoster,
  importManualRosters,
  loadManualRosterStore,
  manualRosterStorageKey,
  saveManualRosterStore,
} from '../utils/manualRosterStorage';
import {
  ManualPastePreview,
  previewManualRosterPaste,
} from '../utils/manualRosterPaste';
import {
  MANUAL_SLOT_ORDER,
  autoAssignManualPlayers,
  availableSlotsForPosition,
  findAutomaticSlot,
  manualSlotLabel,
  sortManualRosterPlayers,
} from '../utils/manualRosterSlots';
import LineupResults from './LineupResults';

interface CatalogSearchProps {
  catalog: ManualCatalogPlayer[];
  excludedIds: Set<string>;
  onSelect: (player: ManualCatalogPlayer) => void;
  placeholder: string;
  buttonLabel: string;
}

const CatalogSearch: React.FC<CatalogSearchProps> = ({
  catalog, excludedIds, onSelect, placeholder, buttonLabel,
}) => {
  const [query, setQuery] = useState('');
  const results = useMemo(() => {
    const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    return catalog.filter((player) => {
      if (excludedIds.has(player.player_id)) return false;
      const haystack = `${player.name} ${player.position} ${player.team ?? ''}`.toLowerCase();
      return terms.every((term) => haystack.includes(term));
    }).slice(0, 10);
  }, [catalog, excludedIds, query]);

  return (
    <Box position="relative" w="100%">
      <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={placeholder} />
      {!!results.length && (
        <VStack
          align="stretch"
          gap={0}
          position="absolute"
          top="100%"
          left={0}
          right={0}
          zIndex={20}
          bg="white"
          borderWidth="1px"
          borderRadius="md"
          boxShadow="lg"
          maxH="280px"
          overflowY="auto"
        >
          {results.map((player) => (
            <Button
              key={player.player_id}
              variant="ghost"
              justifyContent="space-between"
              borderRadius={0}
              onClick={() => {
                onSelect(player);
                setQuery('');
              }}
            >
              <Text>{player.name}</Text>
              <Text fontSize="sm" color="gray.500">
                {player.position}{player.team ? ` · ${player.team}` : ''} · {buttonLabel}
              </Text>
            </Button>
          ))}
        </VStack>
      )}
    </Box>
  );
};

const asRosterPlayer = (player: ManualCatalogPlayer): ManualRosterPlayer => ({
  player_id: player.player_id,
  cached_name: player.name,
  cached_position: player.position,
  cached_team: player.team ?? null,
  slot: 'BN',
});

const ManualRosterBuilder: React.FC = () => {
  const browserId = useUUID();
  const storageKey = manualRosterStorageKey(browserId);
  const safeLoad = useCallback(() => {
    try {
      return loadManualRosterStore(window.localStorage, storageKey);
    } catch {
      return emptyManualRosterStore();
    }
  }, [storageKey]);
  const [store, setStore] = useState(safeLoad);
  const [selectedId, setSelectedId] = useState<string | null>(() => safeLoad().rosters[0]?.id ?? null);
  const [newRosterName, setNewRosterName] = useState('My Manual Team');
  const [catalog, setCatalog] = useState<ManualCatalogPlayer[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [result, setResult] = useState<LeagueDataResponse | null>(null);
  const [resultLoading, setResultLoading] = useState(false);
  const [resultError, setResultError] = useState<string | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [jsonText, setJsonText] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [pasteText, setPasteText] = useState('');
  const [pastePreview, setPastePreview] = useState<ManualPastePreview | null>(null);
  const [selectedPasteIds, setSelectedPasteIds] = useState<Set<string>>(new Set());

  const selected = store.rosters.find((roster) => roster.id === selectedId) ?? null;

  useEffect(() => {
    api.getManualPlayers()
      .then((response) => setCatalog(response.players))
      .catch(() => setCatalogError('Failed to load the player catalog.'))
      .finally(() => setCatalogLoading(false));
  }, []);

  useEffect(() => {
    try {
      saveManualRosterStore(window.localStorage, storageKey, store);
    } catch {
      // The editor remains usable in memory if browser storage is blocked.
    }
  }, [storageKey, store]);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key !== storageKey) return;
      const next = safeLoad();
      setStore(next);
      setSelectedId((current) => next.rosters.some((roster) => roster.id === current)
        ? current
        : next.rosters[0]?.id ?? null);
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, [safeLoad, storageKey]);

  const updateSelected = (update: (roster: ManualRoster) => ManualRoster) => {
    if (!selectedId) return;
    const now = new Date().toISOString();
    setStore((current) => ({
      ...current,
      updated_at: now,
      rosters: current.rosters.map((roster) => roster.id === selectedId
        ? { ...update(roster), updated_at: now }
        : roster),
    }));
    setResult(null);
    setResultError(null);
  };

  const createRoster = () => {
    const roster = createManualRoster(newRosterName.trim() || 'My Manual Team');
    setStore((current) => ({
      ...current,
      updated_at: roster.updated_at,
      rosters: [...current.rosters, roster],
    }));
    setSelectedId(roster.id);
    setNewRosterName('My Manual Team');
    setResult(null);
  };

  const addCatalogPlayer = (player: ManualCatalogPlayer) => {
    updateSelected((roster) => {
      if (roster.players.some((entry) => entry.player_id === player.player_id)) return roster;
      const slot = findAutomaticSlot(player.position, roster.players, roster.lineup_limits);
      if (!slot) {
        setRosterError(`No open ${player.position}, flex, or bench slot is available for ${player.name}.`);
        return roster;
      }
      setRosterError(null);
      return { ...roster, players: [...roster.players, { ...asRosterPlayer(player), slot }] };
    });
  };

  const analyzePaste = () => {
    const preview = previewManualRosterPaste(pasteText, catalog);
    setPastePreview(preview);
    setSelectedPasteIds(new Set(preview.matches.map((match) => match.player.player_id)));
  };

  const addPasteMatches = () => {
    if (!pastePreview) return;
    const additions = pastePreview.matches
      .filter((match) => selectedPasteIds.has(match.player.player_id))
      .map((match) => asRosterPlayer(match.player));
    updateSelected((roster) => {
      const existing = new Set(roster.players.map((player) => player.player_id));
      const players = [...roster.players];
      const skipped: string[] = [];
      additions.filter((player) => !existing.has(player.player_id)).forEach((player) => {
        const slot = findAutomaticSlot(player.cached_position, players, roster.lineup_limits);
        if (slot) players.push({ ...player, slot });
        else skipped.push(player.cached_name);
      });
      setRosterError(skipped.length
        ? `No open eligible roster slot for: ${skipped.join(', ')}.`
        : null);
      return { ...roster, players };
    });
    setPasteText('');
    setPastePreview(null);
    setSelectedPasteIds(new Set());
  };

  const optimize = async () => {
    if (!selected) return;
    setResultLoading(true);
    setResultError(null);
    try {
      const response = await api.postManualLineup(browserId, {
        name: selected.name,
        scoring: selected.scoring,
        lineup_limits: selected.lineup_limits,
        players: selected.players.map((player) => ({
          player_id: player.player_id,
          slot: player.slot,
        })),
      });
      setResult(response);
    } catch (error) {
      setResultError(error instanceof Error ? error.message : 'Failed to optimize this roster.');
    } finally {
      setResultLoading(false);
    }
  };

  const exportSelected = () => {
    if (!selected) return;
    const blob = new Blob([exportManualRoster(selected)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${selected.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase() || 'manual-roster'}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const updateLineupLimit = (slot: keyof ManualRosterLimits, rawValue: number) => {
    const value = Math.max(0, Math.min(slot === 'BN' ? 30 : 10, rawValue || 0));
    updateSelected((roster) => {
      const lineupLimits = { ...roster.lineup_limits, [slot]: value };
      const reassigned = autoAssignManualPlayers(roster.players, lineupLimits);
      if (reassigned.overflow.length) {
        setRosterError(
          `That limit would leave no legal slot for: ${reassigned.overflow.map((player) => player.cached_name).join(', ')}.`,
        );
        return roster;
      }
      setRosterError(null);
      return { ...roster, lineup_limits: lineupLimits, players: reassigned.players };
    });
  };

  const changePlayerSlot = (playerId: string, slot: ManualRosterSlot) => {
    updateSelected((roster) => {
      const player = roster.players.find((entry) => entry.player_id === playerId);
      if (!player) return roster;
      const otherPlayers = roster.players.filter((entry) => entry.player_id !== playerId);
      const allowed = availableSlotsForPosition(player.cached_position, roster.lineup_limits);
      const occupied = otherPlayers.filter((entry) => entry.slot === slot).length;
      if (!allowed.includes(slot) || occupied >= roster.lineup_limits[slot]) {
        setRosterError(`The ${manualSlotLabel(slot)} slots are already full.`);
        return roster;
      }
      setRosterError(null);
      return {
        ...roster,
        players: roster.players.map((entry) => entry.player_id === playerId
          ? { ...entry, slot }
          : entry),
      };
    });
  };

  const rosterIds = new Set(selected?.players.map((player) => player.player_id) ?? []);
  const hasStarter = selected?.players.some((player) => player.slot !== 'BN') ?? false;
  const displayedPlayers = selected ? sortManualRosterPlayers(selected.players) : [];

  return (
    <VStack align="stretch" gap={5}>
      <Box bg="white" borderWidth="1px" borderRadius="xl" boxShadow="sm" p={{ base: 4, md: 6 }}>
        <VStack align="stretch" gap={4}>
          <Box>
            <Text fontSize="2xl" fontWeight="bold">Manual Rosters</Text>
            <Text color="gray.600">
              Teams are saved long-term in this browser under your persistent browser identity.
            </Text>
          </Box>

          <HStack align="end" wrap="wrap">
            <FormControl maxW="320px">
              <FormLabel>New roster name</FormLabel>
              <Input value={newRosterName} onChange={(event) => setNewRosterName(event.target.value)} />
            </FormControl>
            <Button colorScheme="blue" onClick={createRoster}>Create Roster</Button>
          </HStack>

          {!!store.rosters.length && (
            <Wrap>
              {store.rosters.map((roster) => (
                <WrapItem key={roster.id}>
                  <Button
                    size="sm"
                    colorScheme={roster.id === selectedId ? 'blue' : 'gray'}
                    onClick={() => {
                      setSelectedId(roster.id);
                      setResult(null);
                    }}
                  >
                    {roster.name}
                  </Button>
                </WrapItem>
              ))}
            </Wrap>
          )}

          {!selected && (
            <Alert status="info" borderRadius="md">
              <AlertIcon />
              <AlertDescription>Create a roster to begin.</AlertDescription>
            </Alert>
          )}
        </VStack>
      </Box>

      {selected && (
        <Box bg="white" borderWidth="1px" borderRadius="xl" boxShadow="sm" p={{ base: 4, md: 6 }}>
          <VStack align="stretch" gap={5}>
            <HStack align="end" wrap="wrap">
              <FormControl flex="1" minW="240px">
                <FormLabel>Roster name</FormLabel>
                <Input
                  value={selected.name}
                  onChange={(event) => updateSelected((roster) => ({ ...roster, name: event.target.value.slice(0, 100) }))}
                />
              </FormControl>
              <ButtonGroup size="sm" flexWrap="wrap">
                <Button onClick={() => {
                  const copy = duplicateManualRoster(selected);
                  setStore((current) => ({ ...current, updated_at: copy.updated_at, rosters: [...current.rosters, copy] }));
                  setSelectedId(copy.id);
                  setResult(null);
                }}>Duplicate</Button>
                <Button onClick={exportSelected}>Export JSON</Button>
                <Button colorScheme="red" variant="outline" onClick={() => {
                  const remaining = store.rosters.filter((roster) => roster.id !== selected.id);
                  setStore({ ...store, updated_at: new Date().toISOString(), rosters: remaining });
                  setSelectedId(remaining[0]?.id ?? null);
                  setResult(null);
                }}>Delete</Button>
              </ButtonGroup>
            </HStack>

            <HStack align="end" wrap="wrap">
              <FormControl maxW="240px">
                <FormLabel>Reception scoring</FormLabel>
                <Select
                  value={selected.scoring.ppr}
                  onChange={(event) => updateSelected((roster) => ({
                    ...roster,
                    scoring: { ...roster.scoring, ppr: Number(event.target.value) as 0 | 0.5 | 1 },
                  }))}
                >
                  <option value={0}>Standard</option>
                  <option value={0.5}>Half PPR</option>
                  <option value={1}>Full PPR</option>
                </Select>
              </FormControl>
              <FormControl maxW="240px">
                <FormLabel>Passing touchdown</FormLabel>
                <Select
                  value={selected.scoring.passing_td_points}
                  onChange={(event) => updateSelected((roster) => ({
                    ...roster,
                    scoring: { ...roster.scoring, passing_td_points: Number(event.target.value) as 4 | 6 },
                  }))}
                >
                  <option value={4}>4 points</option>
                  <option value={6}>6 points</option>
                </Select>
              </FormControl>
            </HStack>

            <Box>
              <Text fontSize="lg" fontWeight="bold">Roster limits</Text>
              <Text fontSize="sm" color="gray.600" mb={3}>
                Added players fill their main position first, then an eligible W/T, W/R/T, or Superflex slot, then the bench.
              </Text>
              <Wrap spacing={3}>
                {MANUAL_SLOT_ORDER.map((slot) => (
                  <WrapItem key={slot}>
                    <FormControl w="105px">
                      <FormLabel fontSize="sm" mb={1}>{manualSlotLabel(slot)}</FormLabel>
                      <Input
                        aria-label={`Roster limit for ${manualSlotLabel(slot)}`}
                        type="number"
                        min={0}
                        max={slot === 'BN' ? 30 : 10}
                        value={selected.lineup_limits[slot]}
                        onChange={(event) => updateLineupLimit(slot, Number(event.target.value))}
                      />
                    </FormControl>
                  </WrapItem>
                ))}
              </Wrap>
            </Box>

            <Divider />

            <Box>
              <Text fontSize="lg" fontWeight="bold" mb={1}>Add players</Text>
              <Text fontSize="sm" color="gray.600" mb={3}>
                Search by player, position, or team. Players are automatically assigned to the first eligible open slot.
              </Text>
              {catalogLoading ? <Spinner /> : catalogError ? (
                <Alert status="error"><AlertIcon />{catalogError}</Alert>
              ) : (
                <CatalogSearch
                  catalog={catalog}
                  excludedIds={rosterIds}
                  onSelect={addCatalogPlayer}
                  placeholder="Search players (for example, Josh Allen)"
                  buttonLabel="Add"
                />
              )}
            </Box>

            <Box>
              <Text fontSize="lg" fontWeight="bold" mb={1}>Paste players from another website</Text>
              <Text fontSize="sm" color="gray.600" mb={3}>
                Paste a full page or selected roster text. Exact and high-confidence fuzzy matches are previewed before anything is added.
              </Text>
              <Textarea
                value={pasteText}
                onChange={(event) => setPasteText(event.target.value)}
                placeholder="Paste copied roster or player-list text here"
                minH="110px"
              />
              <Button mt={2} onClick={analyzePaste} isDisabled={!pasteText.trim() || !catalog.length}>
                Preview Matches
              </Button>
              {pastePreview && (
                <VStack align="stretch" mt={3} gap={2}>
                  <Text fontWeight="semibold">{pastePreview.matches.length} player matches found</Text>
                  <Box borderWidth="1px" borderRadius="md" maxH="240px" overflowY="auto" p={2}>
                    {pastePreview.matches.map((match) => (
                      <Checkbox
                        key={match.player.player_id}
                        display="flex"
                        isChecked={selectedPasteIds.has(match.player.player_id)}
                        onChange={(event) => setSelectedPasteIds((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(match.player.player_id);
                          else next.delete(match.player.player_id);
                          return next;
                        })}
                      >
                        {match.player.name} ({match.player.position}){' '}
                        <Badge colorScheme={match.kind === 'exact' ? 'green' : 'purple'}>{match.kind}</Badge>
                      </Checkbox>
                    ))}
                  </Box>
                  {!!pastePreview.ambiguous.length && (
                    <Alert status="warning" borderRadius="md">
                      <AlertIcon />
                      <AlertDescription>
                        {pastePreview.ambiguous.length} ambiguous line(s) were excluded. Add those players through search instead.
                      </AlertDescription>
                    </Alert>
                  )}
                  <Button colorScheme="purple" alignSelf="start" onClick={addPasteMatches} isDisabled={!selectedPasteIds.size}>
                    Add {selectedPasteIds.size} Selected
                  </Button>
                </VStack>
              )}
            </Box>

            <Divider />

            <Box>
              <Text fontSize="lg" fontWeight="bold">Roster and lineup slots</Text>
              {!selected.players.length ? (
                <Text color="gray.600">No players added yet.</Text>
              ) : (
                <VStack align="stretch" mt={3} gap={2}>
                  {displayedPlayers.map((player) => (
                    <HStack key={player.player_id} borderWidth="1px" borderRadius="md" p={3} wrap="wrap">
                      <Box flex="1" minW="200px">
                        <Text fontWeight="semibold">{player.cached_name}</Text>
                        <Text fontSize="sm" color="gray.500">
                          {player.cached_position}{player.cached_team ? ` · ${player.cached_team}` : ''}
                        </Text>
                      </Box>
                      <Select
                        aria-label={`Lineup slot for ${player.cached_name}`}
                        maxW="180px"
                        value={player.slot}
                        onChange={(event) => changePlayerSlot(
                          player.player_id,
                          event.target.value as ManualRosterSlot,
                        )}
                      >
                        {availableSlotsForPosition(player.cached_position, selected.lineup_limits).map((slot) => (
                          <option key={slot} value={slot}>{manualSlotLabel(slot)}</option>
                        ))}
                      </Select>
                      <Button size="sm" colorScheme="red" variant="ghost" onClick={() => updateSelected((roster) => ({
                        ...roster,
                        players: roster.players.filter((entry) => entry.player_id !== player.player_id),
                      }))}>Remove</Button>
                    </HStack>
                  ))}
                </VStack>
              )}
            </Box>

            {rosterError && <Alert status="warning"><AlertIcon />{rosterError}</Alert>}
            {!hasStarter && !!selected.players.length && (
              <Alert status="warning" borderRadius="md">
                <AlertIcon />Assign at least one player to a starter slot before optimizing.
              </Alert>
            )}
            {resultError && <Alert status="error"><AlertIcon />{resultError}</Alert>}
            <Button
              colorScheme="green"
              size="lg"
              onClick={optimize}
              isLoading={resultLoading}
              isDisabled={!selected.players.length || !hasStarter}
            >
              Optimize This Roster
            </Button>

            <Divider />

            <Box>
              <Text fontSize="lg" fontWeight="bold" mb={2}>JSON backup or family sharing</Text>
              <Text fontSize="sm" color="gray.600" mb={2}>
                Export above, or paste an exported roster here. Imported rosters are added as separate copies.
              </Text>
              <Textarea value={jsonText} onChange={(event) => setJsonText(event.target.value)} placeholder="Paste exported manual roster JSON" />
              {importError && <Text color="red.500" mt={1}>{importError}</Text>}
              <Button mt={2} onClick={() => {
                try {
                  const imported = importManualRosters(jsonText);
                  setStore((current) => ({
                    ...current,
                    updated_at: new Date().toISOString(),
                    rosters: [...current.rosters, ...imported],
                  }));
                  setSelectedId(imported[0].id);
                  setJsonText('');
                  setImportError(null);
                  setResult(null);
                } catch (error) {
                  setImportError(error instanceof Error ? error.message : 'Invalid roster JSON');
                }
              }} isDisabled={!jsonText.trim()}>Import JSON</Button>
            </Box>
          </VStack>
        </Box>
      )}

      {result && selected && (
        <Box bg="white" borderWidth="1px" borderRadius="xl" boxShadow="sm" p={{ base: 3, md: 5 }}>
          <Text fontSize="xl" fontWeight="bold" textAlign="center" mb={3}>{selected.name}</Text>
          <LineupResults
            borisOptimized={result.boris_optimized ?? result.suggested_starts}
            vegasOptimized={result.vegas_optimized}
            yourLineup={result.your_lineup}
            freeAgentNotice="Roster optimization only — manual teams do not claim league free-agent availability."
          />
        </Box>
      )}
    </VStack>
  );
};

export default ManualRosterBuilder;