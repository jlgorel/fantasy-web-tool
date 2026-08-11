import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
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
  Tr,
  VStack,
} from '@chakra-ui/react';

import { api } from '../api/client';
import {
  CustomCsvPreview,
  CustomDraftSettings,
  LiveDraftPick,
  LiveDraftState,
  RankingsPlayerRow,
  RankingsResponse,
  SimResponse,
} from '../types/draft';
import { SleeperLeagueSummary } from '../types/player';
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
  profileStorageSignature,
  providerProfileMatches,
} from '../utils/draftValueProfile';
import DraftPlayerAvatar from './DraftPlayerAvatar';
import MockDraftView from './MockDraftView';
import PlayerCombobox from './PlayerCombobox';

const MIN_ADP_ONLY_VALUES = 50;

function snakeSlotForPick(pickNo: number, teams: number): number {
  const round = Math.floor((pickNo - 1) / teams);
  const index = (pickNo - 1) % teams;
  return round % 2 === 0 ? index + 1 : teams - index;
}

function adpSourceLabel(source?: string | null): string {
  if (source === 'fantasypros_draftwizard') return 'FantasyPros DraftWizard';
  if (source === 'fantasyfootballcalculator') return 'FantasyFootballCalculator';
  return source || 'loading';
}

const LiveDraftView: React.FC = () => {
  const [entryMode, setEntryMode] = useState<'draft' | 'username' | 'manual'>('draft');
  const [draftId, setDraftId] = useState('');
  const [username, setUsername] = useState('');
  const [connectedUsername, setConnectedUsername] = useState('');
  const [leagues, setLeagues] = useState<SleeperLeagueSummary[]>([]);
  const [selectedLeague, setSelectedLeague] = useState('');
  const [selectedSlot, setSelectedSlot] = useState<number | undefined>();
  const [live, setLive] = useState<LiveDraftState | null>(null);
  const liveRef = useRef<LiveDraftState | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [loading, setLoading] = useState(false);
  const [polling, setPolling] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [boardPpr, setBoardPpr] = useState(0.5);
  const [boardPassingTd, setBoardPassingTd] = useState(4);
  const [manualPicks, setManualPicks] = useState<LiveDraftPick[]>([]);
  const [manualDraftWarning, setManualDraftWarning] = useState<string | null>(null);

  const [players, setPlayers] = useState<RankingsPlayerRow[]>([]);
  const [sources, setSources] = useState<RankingsResponse['sources']>();
  const [customSettings, setCustomSettings] = useState<CustomDraftSettings>(
    emptyCustomDraftSettings(),
  );
  const [elBobertoPaste, setElBobertoPaste] = useState('');
  const [elBobertoPreview, setElBobertoPreview] = useState<CustomCsvPreview | null>(null);
  const [manualPlayerId, setManualPlayerId] = useState('');
  const [manualValue, setManualValue] = useState('');
  const [sim, setSim] = useState<SimResponse | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const lastAutoSimKey = useRef('');
  const initializedDraftId = useRef('');

  useEffect(() => { liveRef.current = live; }, [live]);

  useEffect(() => {
    if (!live?.draft_id || !live.config) return;
    if (initializedDraftId.current !== live.draft_id) {
      initializedDraftId.current = live.draft_id;
      setBoardPpr(live.config.ppr);
      setManualPicks([]);
      setManualDraftWarning(null);
    }
  }, [live?.draft_id, live?.config]);

  useEffect(() => {
    if (!live?.picks?.length || !manualPicks.length) return;
    const authoritativeIds = new Set(live.picks.map((pick) => pick.player_id));
    const authoritativeByPick = new Map(
      live.picks.map((pick) => [pick.pick_no, pick.player_id]),
    );
    let conflict = false;
    const remaining = manualPicks.filter((pick) => {
      if (authoritativeIds.has(pick.player_id)) return false;
      if (authoritativeByPick.has(pick.pick_no)) {
        conflict = true;
        return false;
      }
      return true;
    });
    if (remaining.length !== manualPicks.length) {
      setManualPicks(remaining);
      if (conflict) {
        setManualDraftWarning(
          'Sleeper reported a different player for a manually advanced pick; the local placeholder was reconciled.',
        );
      }
    }
  }, [live?.picks, manualPicks]);

  const applyLiveResponse = useCallback((response: LiveDraftState) => {
    setLive((previous) => response.changed ? response : (
      previous ? { ...previous, ...response } : response
    ));
    setLastUpdated(new Date());
    setError(null);
  }, []);

  const loadDirectDraft = useCallback(async (force = true) => {
    const id = (liveRef.current?.draft_id || draftId).trim();
    if (!id) {
      setError('Enter a Sleeper draft ID.');
      return;
    }
    force ? setLoading(true) : setPolling(true);
    try {
      const current = liveRef.current;
      const response = await api.getLiveDraft(id, {
        username: connectedUsername || undefined,
        slot: selectedSlot,
        knownLastPicked: force ? undefined : current?.last_picked,
        knownStatus: force ? undefined : current?.status,
      });
      setDraftId(response.draft_id || id);
      applyLiveResponse(response);
    } catch (requestError: any) {
      setError(requestError?.message || 'Failed to load Sleeper draft.');
    } finally {
      setLoading(false);
      setPolling(false);
    }
  }, [applyLiveResponse, connectedUsername, draftId, selectedSlot]);

  const findLeagues = async () => {
    if (!username.trim()) {
      setError('Enter a Sleeper username.');
      return;
    }
    setLoading(true);
    try {
      const response = await api.getSleeperUserLeagues(
        username.trim(), undefined, true,
      );
      setLeagues(response.leagues || []);
      setSelectedLeague(response.leagues?.[0]?.league_id || '');
      setError(response.leagues?.length ? null : 'No current redraft/keeper leagues found.');
    } catch (requestError: any) {
      setError(requestError?.message || 'Failed to load Sleeper leagues.');
    } finally {
      setLoading(false);
    }
  };

  const loadLeagueDraft = async () => {
    if (!selectedLeague) {
      setError('Select a league.');
      return;
    }
    setLoading(true);
    try {
      const response = await api.getLiveLeagueDraft(selectedLeague, username.trim());
      setConnectedUsername(username.trim());
      setDraftId(response.draft_id);
      setSelectedSlot(response.user_slot || undefined);
      applyLiveResponse(response);
    } catch (requestError: any) {
      setError(requestError?.message || 'No active draft found for that league.');
    } finally {
      setLoading(false);
    }
  };

  // Conditional polling. The backend only fetches picks when last_picked or
  // status changes, and hidden tabs make no requests.
  useEffect(() => {
    const interval = live?.poll_interval_ms;
    if (entryMode === 'manual' || !autoRefresh || !draftId || !interval) return undefined;
    let cancelled = false;
    let timer: number;
    const tick = async () => {
      if (!cancelled && !document.hidden) await loadDirectDraft(false);
      if (!cancelled) timer = window.setTimeout(tick, interval);
    };
    timer = window.setTimeout(tick, interval);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [autoRefresh, draftId, entryMode, live?.last_picked, live?.poll_interval_ms, live?.status, loadDirectDraft]);

  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden && autoRefresh && draftId) loadDirectDraft(false);
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [autoRefresh, draftId, loadDirectDraft]);

  const starterCount = Object.values(live?.config?.slots || {})
    .reduce((total, count) => total + count, 0);
  const liveBenchSize = Math.max(0, (live?.config?.rounds || 0) - starterCount - 2);
  const requestedProfileId = centralizedProfileId(
    live?.config?.slots || {},
    Boolean(live?.config?.superflex),
    liveBenchSize,
    boardPassingTd,
  );

  // Load the matching value/ADP board whenever Sleeper supplies a config.
  useEffect(() => {
    if (!live?.config || !live.season) return;
    let cancelled = false;
    api.getDraftHelpRankings(
      live.season,
      live.config.teams,
      boardPpr,
      live.config.superflex,
      requestedProfileId,
    ).then((response) => {
      if (cancelled) return;
      setPlayers(response.players || []);
      setSources(response.sources);
    }).catch(() => {
      if (!cancelled) setError('Draft connected, but its rankings board could not load.');
    });
    return () => { cancelled = true; };
  }, [boardPassingTd, boardPpr, live?.config, live?.season, requestedProfileId]);

  const storageKey = useMemo(() => {
    if (!live?.config || !live.season) return null;
    const starterCount = Object.values(live.config.slots || {})
      .reduce((total, count) => total + count, 0);
    const benchSize = Math.max(0, live.config.rounds - starterCount - 2);
    const signature = profileStorageSignature(
      live.config.slots || {}, false, benchSize, boardPassingTd,
    );
    return customDraftStorageKey(
      live.season,
      live.config.teams,
      boardPpr,
      live.config.superflex,
      signature,
    );
  }, [boardPassingTd, boardPpr, live?.config, live?.season]);

  useEffect(() => {
    if (!storageKey) return;
    setCustomSettings(loadCustomDraftSettings(window.localStorage, storageKey));
    setElBobertoPaste('');
    setElBobertoPreview(null);
    setSim(null);
  }, [storageKey]);

  const persistCustom = (settings: CustomDraftSettings) => {
    const updated = { ...settings, updated_at: new Date().toISOString() };
    setCustomSettings(updated);
    if (storageKey) saveCustomDraftSettings(window.localStorage, storageKey, updated);
    setSim(null);
    lastAutoSimKey.current = '';
  };

  const valueOverrides = useMemo(() => customValueMap(customSettings), [customSettings]);
  const importedOverrideCount = useMemo(() => Object.values(customSettings.entries)
    .filter((entry) => entry.value !== undefined && entry.source !== 'manual').length,
  [customSettings]);
  const avoidIds = useMemo(() => avoidedPlayerIds(customSettings), [customSettings]);
  const priorityIds = useMemo(() => Object.entries(customSettings.entries)
    .filter(([, entry]) => entry.source === 'manual' && entry.value !== undefined)
    .map(([pid]) => pid), [customSettings]);
  const profileMatches = providerProfileMatches(
    sources?.values?.profile,
    live?.config?.slots || {},
    liveBenchSize,
    boardPassingTd,
  );
  const useProviderValues = !sources ? true : profileMatches;
  const effectivePlayers = useMemo(() => players.map((player) => {
    const value = customSettings.entries[player.player_id]?.value;
    if (value !== undefined) return { ...player, vbd: value };
    if (useProviderValues) return player;
    return { ...player, vbd: null, fpts: null, auction: null, tier: null };
  }), [players, customSettings, useProviderValues]);
  const providerById = useMemo(() => Object.fromEntries(
    players.map((player) => [player.player_id, player]),
  ), [players]);
  const pendingManualPicks = useMemo(() => {
    const authoritativeIds = new Set((live?.picks || []).map((pick) => pick.player_id));
    const authoritativePickNos = new Set((live?.picks || []).map((pick) => pick.pick_no));
    return manualPicks.filter((pick) => (
      !authoritativeIds.has(pick.player_id)
      && !authoritativePickNos.has(pick.pick_no)
    ));
  }, [live?.picks, manualPicks]);
  const displayPicks = useMemo(() => [
    ...(live?.picks || []),
    ...pendingManualPicks,
  ].sort((a, b) => a.pick_no - b.pick_no), [live?.picks, pendingManualPicks]);
  const byId = useMemo(() => {
    const entries: Record<string, RankingsPlayerRow> = Object.fromEntries(
      effectivePlayers.map((player) => [player.player_id, player]),
    );
    displayPicks.forEach((pick) => {
      if (!entries[pick.player_id]) {
        entries[pick.player_id] = {
          player_id: pick.player_id,
          name: pick.name,
          pos: pick.pos || '',
          team: pick.team,
          vbd: customSettings.entries[pick.player_id]?.value,
        };
      }
    });
    return entries;
  }, [customSettings.entries, displayPicks, effectivePlayers]);
  const drafted = useMemo(() => new Set([
    ...(live?.drafted_ids || []),
    ...pendingManualPicks.map((pick) => pick.player_id),
  ]), [live?.drafted_ids, pendingManualPicks]);
  const displayCurrentPick = (live?.current_pick || 1) + pendingManualPicks.length;
  const displayOnClockSlot = live?.config && displayCurrentPick <= (live.total_picks || 0)
    ? snakeSlotForPick(displayCurrentPick, live.config.teams)
    : null;
  const displayFuturePicks = (live?.my_upcoming_picks || []).filter(
    (pick) => pick >= displayCurrentPick,
  );
  const displayPicksUntilUser = displayFuturePicks.length
    ? Math.max(0, displayFuturePicks[0] - displayCurrentPick)
    : null;
  const displayIsUserPick = displayFuturePicks[0] === displayCurrentPick;
  const displayMyRosterIds = useMemo(() => [
    ...(live?.my_roster_ids || []),
    ...pendingManualPicks
      .filter((pick) => pick.optimistic_owner_is_user || pick.draft_slot === live?.user_slot)
      .map((pick) => pick.player_id),
  ], [live?.my_roster_ids, live?.user_slot, pendingManualPicks]);
  const available = useMemo(() => effectivePlayers
    .filter((player) => !drafted.has(player.player_id))
    .sort((a, b) => (a.adp ?? 9999) - (b.adp ?? 9999)),
  [effectivePlayers, drafted]);
  const valueCount = effectivePlayers.filter((player) => player.vbd != null).length;
  const adpOnly = sources?.values?.source === 'custom upload required';
  const customProfileRequired = adpOnly || !useProviderValues;
  const valuesReady = customProfileRequired ? valueCount >= MIN_ADP_ONLY_VALUES : valueCount > 0;

  const rosterSlots = useMemo(() => buildRosterSlots(
    displayMyRosterIds,
    byId,
    live?.config?.slots || {},
    live?.config?.rounds || 15,
  ), [byId, displayMyRosterIds, live?.config?.rounds, live?.config?.slots]);
  const confidence = confidencePresentation(sim?.recommendation_confidence);

  const manuallyDraft = (player: RankingsPlayerRow) => {
    if (!live?.config || !displayOnClockSlot || displayCurrentPick > (live.total_picks || 0)) return;
    const round = Math.floor((displayCurrentPick - 1) / live.config.teams) + 1;
    setManualPicks((current) => [...current, {
      pick_no: displayCurrentPick,
      round,
      draft_slot: displayOnClockSlot,
      player_id: player.player_id,
      name: player.name,
      pos: player.pos,
      team: player.team,
      picked_by: null,
      is_keeper: false,
      optimistic: true,
      optimistic_owner_is_user: displayIsUserPick,
    }]);
    setManualDraftWarning(null);
    setSim(null);
    lastAutoSimKey.current = '';
  };

  const undoManualDraft = () => {
    setManualPicks((current) => current.slice(0, -1));
    setManualDraftWarning(null);
    setSim(null);
    lastAutoSimKey.current = '';
  };

  const recommend = useCallback(async () => {
    if (!live?.config || !live.user_slot || !live.current_pick || !valuesReady) return;
    setSimLoading(true);
    try {
      const response = await api.postDraftHelpSim({
        year: live.season || String(new Date().getFullYear()),
        teams: live.config.teams,
        rounds: live.config.rounds,
        my_slot: live.user_slot,
        ppr: boardPpr,
        superflex: live.config.superflex,
        slots: live.config.slots,
        drafted_ids: Array.from(drafted),
        my_roster_ids: displayMyRosterIds,
        current_pick: displayCurrentPick,
        my_future_pick_numbers: displayFuturePicks,
        n_sims: 60,
        top_k: 8,
        seed: displayCurrentPick,
        value_overrides: valueOverrides,
        use_provider_values: useProviderValues,
        profile_id: requestedProfileId || undefined,
        avoid_ids: avoidIds,
        priority_candidate_ids: priorityIds,
      });
      setSim(response);
    } catch (requestError: any) {
      setError(requestError?.message || 'Recommendation failed.');
    } finally {
      setSimLoading(false);
    }
  }, [avoidIds, boardPpr, displayCurrentPick, displayFuturePicks, displayMyRosterIds, drafted, live, priorityIds, requestedProfileId, useProviderValues, valueOverrides, valuesReady]);

  // Run once per changed on-clock state/value revision. Manual refresh remains.
  useEffect(() => {
    if (!displayIsUserPick || !valuesReady || !live) return;
    const key = `${live.draft_id}:${live.last_picked}:${displayCurrentPick}:${pendingManualPicks.length}:${customSettings.updated_at}`;
    if (lastAutoSimKey.current === key) return;
    lastAutoSimKey.current = key;
    recommend();
  }, [customSettings.updated_at, displayCurrentPick, displayIsUserPick, live, pendingManualPicks.length, recommend, valuesReady]);

  const previewElBobertoPaste = () => {
    setElBobertoPreview(
      previewElBobertoValues(elBobertoPaste, players),
    );
  };

  const applyElBoberto = () => {
    if (!elBobertoPreview) return;
    const entries = { ...customSettings.entries };
    elBobertoPreview.matches.forEach((match) => {
      if (!match.player_id || match.value === undefined || match.error) return;
      const existing = entries[match.player_id];
      if (existing?.source === 'manual' && existing.value !== undefined) return;
      entries[match.player_id] = {
        ...existing,
        value: match.value,
        source: 'elboberto_paste',
      };
    });
    persistCustom({ ...customSettings, entries });
  };

  const clearImportedValues = () => {
    const entries: CustomDraftSettings['entries'] = {};
    Object.entries(customSettings.entries).forEach(([playerId, entry]) => {
      if (entry.source === 'manual') entries[playerId] = entry;
      else if (entry.avoid) entries[playerId] = { avoid: true, source: 'manual' };
    });
    persistCustom({ ...customSettings, entries });
    setElBobertoPreview(null);
  };

  const saveManual = () => {
    const value = Number(manualValue);
    if (!manualPlayerId || !Number.isFinite(value)) return;
    persistCustom({
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
  };

  const toggleAvoid = (playerId: string) => {
    const existing = customSettings.entries[playerId];
    const avoid = !existing?.avoid;
    const entries = { ...customSettings.entries };
    const next = { ...existing, avoid: avoid || undefined, source: existing?.source || 'manual' };
    if (next.value === undefined && !next.avoid) delete entries[playerId];
    else entries[playerId] = next;
    persistCustom({ ...customSettings, entries });
  };

  const modeButtons = (
    <HStack spacing={2} flexWrap="wrap">
      <Button size="sm" colorScheme={entryMode === 'draft' ? 'blue' : 'gray'} onClick={() => setEntryMode('draft')}>
        Sleeper draft ID
      </Button>
      <Button size="sm" colorScheme={entryMode === 'username' ? 'blue' : 'gray'} onClick={() => setEntryMode('username')}>
        Sleeper username
      </Button>
      <Button size="sm" colorScheme={entryMode === 'manual' ? 'blue' : 'gray'} onClick={() => setEntryMode('manual')}>
        Custom room
      </Button>
    </HStack>
  );

  if (entryMode === 'manual') {
    return (
      <VStack align="stretch" spacing={4}>
        <Box borderWidth="1px" borderRadius="md" p={3}>{modeButtons}</Box>
        <MockDraftView />
      </VStack>
    );
  }

  return (
    <VStack align="stretch" spacing={4}>
      <Box borderWidth="1px" borderRadius="md" p={3}>
        <Box mb={3}>{modeButtons}</Box>
        {entryMode === 'draft' ? (
          <HStack align="flex-end" flexWrap="wrap">
            <Box flex="1" minW="260px">
              <Text fontSize="xs" color="gray.500">Sleeper draft ID or draft URL</Text>
              <Input
                size="sm"
                value={draftId}
                placeholder="1392134959602356224"
                onChange={(event) => {
                  const match = event.target.value.match(/(\d{8,})/);
                  setDraftId(match ? match[1] : event.target.value.trim());
                }}
              />
            </Box>
            <Button size="sm" colorScheme="blue" onClick={() => loadDirectDraft(true)} isLoading={loading}>
              Connect
            </Button>
          </HStack>
        ) : (
          <VStack align="stretch" spacing={2}>
            <HStack>
              <Input size="sm" value={username} placeholder="Sleeper username" onChange={(e) => setUsername(e.target.value)} />
              <Button size="sm" onClick={findLeagues} isLoading={loading}>Find leagues</Button>
            </HStack>
            {!!leagues.length && (
              <HStack>
                <Select size="sm" value={selectedLeague} onChange={(e) => setSelectedLeague(e.target.value)}>
                  {leagues.map((league) => (
                    <option key={league.league_id} value={league.league_id}>
                      {league.name || league.league_id}
                    </option>
                  ))}
                </Select>
                <Button size="sm" colorScheme="blue" onClick={loadLeagueDraft} isLoading={loading}>
                  Find active draft
                </Button>
              </HStack>
            )}
          </VStack>
        )}
      </Box>

      {error && <Alert status="error"><AlertIcon />{error}</Alert>}

      {live?.needs_slot && (
        <Alert status="warning" alignItems="flex-end">
          <AlertIcon />
          <Box flex="1">
            <Text fontSize="sm">Select which draft slot is yours.</Text>
            <Select size="sm" mt={1} value={selectedSlot || ''} onChange={(e) => setSelectedSlot(Number(e.target.value))}>
              <option value="">Choose slot…</option>
              {(live.available_slots || []).map((slot) => <option key={slot} value={slot}>Slot {slot}</option>)}
            </Select>
          </Box>
          <Button ml={2} size="sm" onClick={() => loadDirectDraft(true)} isDisabled={!selectedSlot}>Use slot</Button>
        </Alert>
      )}

      {live && (
        <>
          <Box borderWidth="1px" borderRadius="md" p={3}>
            <HStack justify="space-between" align="flex-start" flexWrap="wrap">
              <Box>
                <Heading size="sm">{live.name}</Heading>
                <HStack mt={1} spacing={2} flexWrap="wrap">
                  <Badge colorScheme={live.status === 'drafting' ? 'green' : live.status === 'paused' ? 'orange' : 'gray'}>{live.status}</Badge>
                  {displayCurrentPick && <Badge>Pick {displayCurrentPick}/{live.total_picks}</Badge>}
                  {displayOnClockSlot && <Badge>Slot {displayOnClockSlot} on clock</Badge>}
                  {live.user_slot && <Badge colorScheme="blue">Your slot: {live.user_slot}</Badge>}
                  {displayIsUserPick && <Badge colorScheme="green">YOUR PICK</Badge>}
                  {displayPicksUntilUser != null && !displayIsUserPick && (
                    <Badge colorScheme="purple">{displayPicksUntilUser} picks until you</Badge>
                  )}
                  {!!pendingManualPicks.length && <Badge colorScheme="orange">{pendingManualPicks.length} awaiting Sleeper</Badge>}
                </HStack>
                <Text mt={1} fontSize="xs" color="gray.500">
                  {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Not refreshed yet'}
                  {polling ? ' · checking Sleeper…' : ''}
                </Text>
              </Box>
              <HStack>
                <Checkbox isChecked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)}>Auto-refresh</Checkbox>
                <Button size="sm" onClick={undoManualDraft} isDisabled={!pendingManualPicks.length}>Undo local pick</Button>
                <Button size="sm" onClick={() => loadDirectDraft(true)} isLoading={loading}>Refresh now</Button>
                <Button size="sm" colorScheme="green" onClick={recommend} isLoading={simLoading} isDisabled={!live.user_slot || !valuesReady || !displayIsUserPick} title={displayIsUserPick ? undefined : 'Recommendations run when your pick is on the clock'}>
                  Recommend
                </Button>
              </HStack>
            </HStack>
            {live.username_warning && <Text mt={2} fontSize="xs" color="orange.700">{live.username_warning}</Text>}
          </Box>

          <Alert status="info" alignItems="flex-start">
            <AlertIcon />
            <Box fontSize="sm">
              Sleeper&apos;s public API can lag behind the draft room even though this
              page checks every {live.poll_interval_ms === 5000 ? 'five' : '20'} seconds.
              Use <b>Mark drafted</b> to advance this board immediately; the next
              Sleeper update will confirm or reconcile it. Use <b>Undo local pick</b>
              {' '}after a misclick.
            </Box>
          </Alert>
          {manualDraftWarning && <Alert status="warning"><AlertIcon />{manualDraftWarning}</Alert>}

          <HStack borderWidth="1px" borderRadius="md" p={3} spacing={3} flexWrap="wrap">
            <Box>
              <Text fontSize="xs" color="gray.500">ADP scoring source</Text>
              <Select aria-label="ADP scoring source" size="sm" value={boardPpr} onChange={(e) => setBoardPpr(Number(e.target.value))} w="145px">
                <option value={0}>Standard</option>
                <option value={0.5}>Half PPR</option>
                <option value={1}>Full PPR</option>
              </Select>
            </Box>
            <Box>
              <Text fontSize="xs" color="gray.500">Passing TD</Text>
              <Select aria-label="Passing touchdown scoring" size="sm" value={boardPassingTd} onChange={(e) => setBoardPassingTd(Number(e.target.value))} w="100px">
                <option value={4}>4 points</option>
                <option value={6}>6 points</option>
              </Select>
            </Box>
            <Text fontSize="xs" color="gray.500">
              Sleeper detected {live.config?.ppr === 1 ? 'Full PPR' : live.config?.ppr === 0.5 ? 'Half PPR' : 'Standard'}.
              Change this if the draft metadata is wrong or you prefer another market.
            </Text>
          </HStack>

          <HStack spacing={2} flexWrap="wrap">
            <Badge colorScheme={adpOnly ? 'orange' : 'blue'}>
              Values: {sources?.values?.source || 'loading'}
              {sources?.values?.source_version ? ` v${sources.values.source_version}` : ''}
            </Badge>
            <Badge colorScheme="green">ADP: {adpSourceLabel(sources?.adp?.source)}</Badge>
            <Text fontSize="xs" color="gray.500">{valueCount} player values loaded</Text>
          </HStack>
          {sources?.values?.provider && !useProviderValues && (
            <Alert status="warning">
              <AlertIcon />
              <Box fontSize="sm">
                Sleeper&apos;s exact starter/bench/passing-TD profile is not in the
                published provider blob. Provider VBD is disabled; paste at least{' '}
                {MIN_ADP_ONLY_VALUES} finished ElBoberto values configured for this league
                ({valueCount} loaded).
              </Box>
            </Alert>
          )}
          {sources?.values?.provider && useProviderValues && importedOverrideCount > 0 && (
            <Alert status="warning">
              <AlertIcon />
              <Box flex="1" fontSize="sm">
                {importedOverrideCount} saved bulk values override {sources.values.source || 'provider'} AvgVBD.
              </Box>
              <Button size="xs" onClick={clearImportedValues}>Use provider values</Button>
            </Alert>
          )}
          {!valuesReady && (
            <Alert status="warning"><AlertIcon />Paste at least {MIN_ADP_ONLY_VALUES} finished ElBoberto Value/VORP rows before requesting recommendations.</Alert>
          )}

          <Accordion allowToggle borderWidth="1px" borderRadius="md">
            <AccordionItem border="none">
              <AccordionButton><Box flex="1" textAlign="left" color="gray.800"><b>Live draft values &amp; preferences</b> · {valueCount} values · {avoidIds.length} avoided</Box><AccordionIcon /></AccordionButton>
              <AccordionPanel>
                <VStack align="stretch" spacing={3}>
                  <Box>
                    <Text fontSize="sm" fontWeight="semibold" mb={1}>Paste custom ElBoberto values</Text>
                    <Text fontSize="xs" color="gray.600" mb={2}>
                      Configure ElBoberto for this league, open <b>CheatSheet</b>, copy the
                      used table (or <b>OVR / Player / Pos / VBD</b>), and paste it below.
                      Settings are browser-local and shared with the Custom room only for
                      this exact league profile.
                    </Text>
                    <Textarea
                      size="sm"
                      value={elBobertoPaste}
                      onChange={(event) => {
                        setElBobertoPaste(event.target.value);
                        setElBobertoPreview(null);
                      }}
                      placeholder={'OVR\tPlayer\tPos\tVBD\n1\tJahmyr Gibbs\tRB\t211.01'}
                      minH="110px"
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
                      <Box mt={2}>
                        <HStack flexWrap="wrap">
                          <Badge colorScheme="green">
                            {elBobertoPreview.matches.filter((match) => match.player_id && !match.error).length} matched
                          </Badge>
                          <Badge colorScheme="orange">
                            {elBobertoPreview.matches.filter((match) => match.error).length} skipped
                          </Badge>
                          <Button
                            size="xs"
                            onClick={applyElBoberto}
                            isDisabled={!!elBobertoPreview.errors.length || !elBobertoPreview.matches.some((match) => match.player_id && !match.error)}
                          >
                            Apply values
                          </Button>
                        </HStack>
                        {elBobertoPreview.errors.map((message) => (
                          <Text key={message} fontSize="xs" color="red.600" mt={1}>{message}</Text>
                        ))}
                        {elBobertoPreview.matches.filter((match) => match.error).slice(0, 6).map((match) => (
                          <Text key={`${match.row}-${match.input_name}`} fontSize="xs" color="orange.700" mt={1}>
                            Row {match.row}: {match.input_name || '(blank)'} — {match.error}
                          </Text>
                        ))}
                      </Box>
                    )}
                  </Box>
                  <HStack flexWrap="wrap">
                    <PlayerCombobox
                      players={effectivePlayers}
                      value={manualPlayerId}
                      onChange={(pid) => {
                        setManualPlayerId(pid);
                        const current = customSettings.entries[pid]?.value;
                        setManualValue(current === undefined ? '' : String(current));
                      }}
                    />
                    <Input size="sm" type="number" maxW="120px" value={manualValue} placeholder="Value" onChange={(e) => setManualValue(e.target.value)} />
                    <Button size="sm" onClick={saveManual}>Save</Button>
                  </HStack>
                </VStack>
              </AccordionPanel>
            </AccordionItem>
          </Accordion>

          {sim?.recommendation && (
            <Box borderWidth="1px" borderColor="green.300" bg="green.50" borderRadius="md" p={3}>
              <Heading size="sm" mb={2}>Recommended: {sim.recommendation.name} ({sim.recommendation.pos})</Heading>
              {confidence && (
                <HStack mb={2}>
                  <Badge colorScheme={confidence.color}>{confidence.label}</Badge>
                  <Text fontSize="xs" color="gray.600">{confidence.detail}</Text>
                  {sim.cache_hit && <Badge colorScheme="gray">cached state</Badge>}
                </HStack>
              )}
              <Table size="sm"><Thead><Tr><Th>Player</Th><Th>Pos</Th><Th isNumeric>ADP</Th><Th isNumeric>Used VBD</Th><Th isNumeric title="Middle 50% rollout range is shown below the mean">Lineup VAL</Th></Tr></Thead>
                <Tbody>{sim.candidates.map((candidate, index) => (
                  <Tr key={candidate.player_id} bg={index === 0 ? 'green.100' : undefined}>
                    <Td><HStack spacing={2}><DraftPlayerAvatar playerId={candidate.player_id} name={candidate.name} team={byId[candidate.player_id]?.team} size={30} /><Text>{candidate.name}</Text></HStack></Td>
                    <Td>{candidate.pos}</Td><Td isNumeric>{candidate.adp.toFixed(1)}</Td><Td isNumeric>{candidate.proj.toFixed(1)}</Td>
                    <Td isNumeric>{candidate.avg_value.toFixed(1)}{candidate.value_p25 != null && candidate.value_p75 != null && <Text fontSize="2xs" color="gray.500">{candidate.value_p25.toFixed(0)}–{candidate.value_p75.toFixed(0)}</Text>}</Td>
                  </Tr>
                ))}</Tbody></Table>
              {sim.priority_candidates?.filter((target) => !sim.candidates.some(
                (candidate) => candidate.player_id === target.player_id,
              )).map((target) => (
                <Text key={target.player_id} mt={2} fontSize="xs" color="blue.800">
                  Manually adjusted target evaluated: {target.name} ({target.pos}) —
                  VAL {target.avg_value.toFixed(1)}. The model currently
                  prefers taking someone else first and targeting this player later.
                </Text>
              ))}
            </Box>
          )}

          <SimpleGrid columns={{ base: 1, lg: 3 }} spacing={4}>
            <Box gridColumn={{ lg: 'span 2' }}>
              <Heading size="xs" mb={2}>Available by ADP ({available.length})</Heading>
              <Box maxH="620px" overflowY="auto" borderWidth="1px" borderRadius="md">
                <Table size="sm"><Thead position="sticky" top={0} bg="white" zIndex={1}><Tr><Th isNumeric>ADP</Th><Th>Player</Th><Th>Pos</Th><Th isNumeric>Source VBD</Th><Th isNumeric>Used VBD</Th><Th></Th></Tr></Thead>
                  <Tbody>{available.slice(0, 160).map((player) => {
                    const custom = customSettings.entries[player.player_id];
                    return (
                      <Tr key={player.player_id} bg={custom?.avoid ? 'red.50' : undefined}>
                        <Td isNumeric>{player.adp?.toFixed(1)}</Td>
                        <Td><HStack spacing={2}><DraftPlayerAvatar playerId={player.player_id} name={player.name} team={player.team} /><Box>{player.name}{custom?.avoid && <Badge ml={1} colorScheme="red">avoid</Badge>}</Box></HStack></Td>
                        <Td>{player.pos}</Td>
                        <Td isNumeric>{providerById[player.player_id]?.vbd?.toFixed(1) || '—'}</Td>
                        <Td isNumeric color={custom?.value !== undefined ? 'blue.600' : undefined}>
                          {player.vbd?.toFixed(1) || '—'}
                        </Td>
                        <Td>
                          <HStack spacing={1}>
                            <Button size="xs" colorScheme="orange" onClick={() => manuallyDraft(player)}>Mark drafted</Button>
                            <Button size="xs" colorScheme="red" variant="outline" onClick={() => toggleAvoid(player.player_id)}>{custom?.avoid ? 'Allow' : 'Avoid'}</Button>
                          </HStack>
                        </Td>
                      </Tr>
                    );
                  })}</Tbody></Table>
              </Box>
            </Box>
            <Box>
              <Heading size="xs" mb={2}>Your lineup ({displayMyRosterIds.length}/{live.config?.rounds || 15})</Heading>
              <VStack align="stretch" spacing={1} borderWidth="1px" borderRadius="md" p={2} maxH="620px" overflowY="auto">
                {rosterSlots.map((slot, index) => {
                  const color = slot.player
                    ? (POS_COLOR[slot.player.pos] || 'gray')
                    : (SLOT_COLOR[slot.type] || 'gray');
                  return (
                    <HStack
                      key={`${slot.type}-${index}`}
                      justify="space-between"
                      spacing={2}
                      px={2}
                      py={1}
                      borderRadius="sm"
                      borderLeftWidth="4px"
                      borderLeftColor={`${color}.400`}
                      bg={slot.player ? `${color}.50` : 'gray.50'}
                    >
                      <HStack spacing={2} minW={0}>
                        <Badge minW="34px" textAlign="center" colorScheme={SLOT_COLOR[slot.type] || 'gray'}>{slot.label}</Badge>
                        {slot.player && <DraftPlayerAvatar playerId={slot.player.player_id} name={slot.player.name} team={slot.player.team} size={28} />}
                        <Text fontSize="sm" noOfLines={1} color={slot.player ? undefined : 'gray.400'}>{slot.player?.name || 'Empty'}</Text>
                      </HStack>
                      {slot.player && <Badge colorScheme={POS_COLOR[slot.player.pos] || 'gray'}>{slot.player.pos}</Badge>}
                    </HStack>
                  );
                })}
              </VStack>
            </Box>
          </SimpleGrid>

          <Accordion allowToggle borderWidth="1px" borderRadius="md">
            <AccordionItem border="none">
              <AccordionButton>
                <Box flex="1" textAlign="left" color="gray.800">
                  Recent draft picks ({displayPicks.length})
                </Box>
                <AccordionIcon />
              </AccordionButton>
              <AccordionPanel px={0} pb={0}>
                <Box maxH="300px" overflowY="auto">
                  <Table size="sm"><Thead><Tr><Th isNumeric>#</Th><Th>Player</Th><Th>Pos</Th><Th isNumeric>Slot</Th><Th>Status</Th></Tr></Thead>
                    <Tbody>{[...displayPicks].reverse().map((pick) => (
                      <Tr key={`${pick.pick_no}-${pick.player_id}`} bg={pick.draft_slot === live.user_slot ? 'blue.50' : undefined}>
                        <Td isNumeric>{pick.pick_no}</Td><Td>{pick.name}</Td><Td>{pick.pos}</Td><Td isNumeric>{pick.draft_slot}</Td>
                        <Td>{pick.optimistic ? <Badge colorScheme="orange">local</Badge> : <Badge colorScheme="green">Sleeper</Badge>}</Td>
                      </Tr>
                    ))}</Tbody></Table>
                </Box>
              </AccordionPanel>
            </AccordionItem>
          </Accordion>
        </>
      )}

      {(loading || simLoading) && <HStack><Spinner size="sm" /><Text fontSize="sm">Working…</Text></HStack>}
    </VStack>
  );
};

export default LiveDraftView;
