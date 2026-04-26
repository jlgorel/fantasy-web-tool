import React, { useMemo, useState } from "react";
import {
  Badge,
  Box,
  Button,
  HStack,
  Image,
  Input,
  InputGroup,
  InputLeftElement,
  Select,
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
} from "@chakra-ui/react";
import { SearchIcon, TriangleDownIcon, TriangleUpIcon } from "@chakra-ui/icons";
import {
  OverallRankingsPayload,
  PlayerRow,
  StatValueRaw,
} from "../types/player";

export type { OverallRankingsPayload, PlayerRow };

interface Props {
  rankings: OverallRankingsPayload["overall_rankings"] | null;
  /** Optional fixed variant (e.g. used in tests). When omitted, an in-component selector controls it. */
  variantKey?: string;
  loading?: boolean;
}

// ---------- variant model ----------
type PprMode = "std" | "halfppr" | "fullppr";
type PassTd = "4" | "6";
const PPR_LABEL: Record<PprMode, string> = {
  std: "Standard",
  halfppr: "Half-PPR",
  fullppr: "Full-PPR",
};
const variantToKey = (ppr: PprMode, td: PassTd) => `${ppr}_${td}ptpass`;
const DEFAULT_PPR: PprMode = "fullppr";
const DEFAULT_TD: PassTd = "4";

// ---------- stat parsing ----------
interface ParsedStat {
  num: number | null;
  display: string;
  isBackup: boolean;
}

function parseStat(val: StatValueRaw): ParsedStat {
  if (val === null || val === undefined) return { num: null, display: "", isBackup: false };
  if (typeof val === "number") return { num: val, display: formatNum(val), isBackup: false };
  if (typeof val === "string") {
    if (val.startsWith("BACKUP_")) {
      const num = Number(val.slice("BACKUP_".length));
      return Number.isNaN(num)
        ? { num: null, display: val, isBackup: true }
        : { num, display: formatNum(num), isBackup: true };
    }
    const maybe = Number(val);
    return Number.isNaN(maybe)
      ? { num: null, display: val, isBackup: false }
      : { num: maybe, display: formatNum(maybe), isBackup: false };
  }
  return { num: null, display: String(val), isBackup: false };
}

function formatNum(n: number): string {
  if (Math.abs(n) >= 100) return n.toFixed(0);
  if (Math.abs(n) >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

// ---------- column model ----------
interface Column {
  key: string;
  label: string;
  numeric: boolean;
  /** Stat name in the PROJ dict (omit for derived/special columns) */
  statKey?: string;
  /** Override accessor for non-stat columns */
  value?: (row: PlayerRow, rank: number) => number | string | null;
}

const COL_RANK: Column = { key: "RANK", label: "#", numeric: true, value: (_r, rank) => rank };
const COL_NAME: Column = { key: "NAME", label: "Player", numeric: false, value: (r) => r.NAME };
const COL_POS: Column = { key: "POS", label: "Pos", numeric: false, value: (r) => r.POS ?? "" };
const COL_VEGAS: Column = {
  key: "VEGAS",
  label: "Vegas Pts",
  numeric: true,
  value: (r) => r.VEGAS ?? null,
};

const STAT_COLS: Record<string, Column> = {
  PASS_YDS: { key: "PASS_YDS", label: "Pass Yds", numeric: true, statKey: "Passing Yards" },
  PASS_TD: { key: "PASS_TD", label: "Pass TD", numeric: true, statKey: "Passing Touchdowns" },
  INT: { key: "INT", label: "INT", numeric: true, statKey: "Interceptions" },
  RUSH_YDS: { key: "RUSH_YDS", label: "Rush Yds", numeric: true, statKey: "Rushing Yards" },
  REC: { key: "REC", label: "Rec", numeric: true, statKey: "Receptions" },
  REC_YDS: { key: "REC_YDS", label: "Rec Yds", numeric: true, statKey: "Receiving Yards" },
  TD: { key: "TD", label: "TD", numeric: true, statKey: "Anytime Touchdown" },
};

type PositionTab = "ALL" | "QB" | "RB" | "WR" | "TE";

const COLUMNS_BY_TAB: Record<PositionTab, Column[]> = {
  ALL: [
    COL_RANK,
    COL_NAME,
    COL_POS,
    STAT_COLS.PASS_YDS,
    STAT_COLS.PASS_TD,
    STAT_COLS.RUSH_YDS,
    STAT_COLS.REC,
    STAT_COLS.REC_YDS,
    STAT_COLS.TD,
    COL_VEGAS,
  ],
  QB: [
    COL_RANK,
    COL_NAME,
    STAT_COLS.PASS_YDS,
    STAT_COLS.PASS_TD,
    STAT_COLS.INT,
    STAT_COLS.RUSH_YDS,
    STAT_COLS.TD,
    COL_VEGAS,
  ],
  RB: [
    COL_RANK,
    COL_NAME,
    STAT_COLS.RUSH_YDS,
    STAT_COLS.REC,
    STAT_COLS.REC_YDS,
    STAT_COLS.TD,
    COL_VEGAS,
  ],
  WR: [
    COL_RANK,
    COL_NAME,
    STAT_COLS.REC,
    STAT_COLS.REC_YDS,
    STAT_COLS.RUSH_YDS,
    STAT_COLS.TD,
    COL_VEGAS,
  ],
  TE: [
    COL_RANK,
    COL_NAME,
    STAT_COLS.REC,
    STAT_COLS.REC_YDS,
    STAT_COLS.TD,
    COL_VEGAS,
  ],
};

// Get the comparable numeric value for sorting; falls back to string compare for non-numeric.
function sortValue(row: PlayerRow, col: Column, rank: number): number | string | null {
  if (col.value) return col.value(row, rank);
  if (col.statKey) {
    const parsed = parseStat(row.PROJ?.[col.statKey] ?? null);
    return parsed.num;
  }
  return null;
}

// ---------- main component ----------
export default function OverallRankingsTable({
  rankings,
  variantKey,
  loading = false,
}: Props) {
  // Local variant state (used only when no fixed variantKey is passed in)
  const [pprMode, setPprMode] = useState<PprMode>(DEFAULT_PPR);
  const [passTd, setPassTd] = useState<PassTd>(DEFAULT_TD);
  const effectiveVariantKey = variantKey ?? variantToKey(pprMode, passTd);

  const [tab, setTab] = useState<PositionTab>("ALL");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<string>("RANK");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const allRows: PlayerRow[] = useMemo(() => {
    if (!rankings) return [];
    return rankings[effectiveVariantKey] ?? [];
  }, [rankings, effectiveVariantKey]);

  // Position-filtered rows. Keep the original VEGAS-sorted order so RANK # is meaningful.
  const positionRows = useMemo(() => {
    if (tab === "ALL") return allRows;
    return allRows.filter((r) => (r.POS ?? "").toUpperCase() === tab);
  }, [allRows, tab]);

  // Apply search filter
  const searchedRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return positionRows;
    return positionRows.filter((r) => r.NAME.toLowerCase().includes(q));
  }, [positionRows, search]);

  // Pre-compute the rank index *within the position group* before sorting,
  // so the # column always shows positional rank order even after re-sorting.
  const indexedRows = useMemo(
    () => searchedRows.map((row, i) => ({ row, rank: i + 1 })),
    [searchedRows]
  );

  // Apply sort
  const sortedRows = useMemo(() => {
    const cols = COLUMNS_BY_TAB[tab];
    const col = cols.find((c) => c.key === sortKey);
    if (!col) return indexedRows;
    const dir = sortDir === "asc" ? 1 : -1;
    const copy = [...indexedRows];
    copy.sort((a, b) => {
      const va = sortValue(a.row, col, a.rank);
      const vb = sortValue(b.row, col, b.rank);
      if (va === null && vb === null) return 0;
      if (va === null) return 1; // nulls always last
      if (vb === null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
    return copy;
  }, [indexedRows, sortKey, sortDir, tab]);

  // When tab changes, reset sort to the natural order (rank ascending).
  React.useEffect(() => {
    setSortKey("RANK");
    setSortDir("asc");
  }, [tab]);

  if (loading) {
    return (
      <VStack py={10}>
        <Spinner size="xl" />
        <Text>Loading rankings...</Text>
      </VStack>
    );
  }

  const cols = COLUMNS_BY_TAB[tab];
  const handleHeaderClick = (col: Column) => {
    if (sortKey === col.key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(col.key);
      // Default: numeric columns descending (highest first), text columns ascending
      setSortDir(col.numeric ? "desc" : "asc");
    }
  };

  return (
    <Box px={{ base: 2, md: 4 }} py={4}>
      <VStack align="stretch" spacing={3}>
        {/* Controls row */}
        <HStack
          spacing={3}
          flexWrap="wrap"
          justify="space-between"
          bg="white"
          borderWidth="1px"
          borderRadius="md"
          px={3}
          py={2}
        >
          <HStack spacing={2} flexWrap="wrap">
            <Text fontWeight="semibold">Scoring:</Text>
            {!variantKey && (
              <>
                <Select
                  size="sm"
                  value={pprMode}
                  onChange={(e) => setPprMode(e.target.value as PprMode)}
                  width="140px"
                >
                  {(Object.keys(PPR_LABEL) as PprMode[]).map((m) => (
                    <option key={m} value={m}>
                      {PPR_LABEL[m]}
                    </option>
                  ))}
                </Select>
                <Select
                  size="sm"
                  value={passTd}
                  onChange={(e) => setPassTd(e.target.value as PassTd)}
                  width="120px"
                >
                  <option value="4">4pt Pass TD</option>
                  <option value="6">6pt Pass TD</option>
                </Select>
              </>
            )}
            {variantKey && <Badge>{variantKey}</Badge>}
          </HStack>

          <InputGroup size="sm" maxW="260px">
            <InputLeftElement pointerEvents="none">
              <SearchIcon color="gray.400" />
            </InputLeftElement>
            <Input
              placeholder="Search player..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </InputGroup>
        </HStack>

        {/* Position tabs */}
        <HStack spacing={2} flexWrap="wrap">
          {(["ALL", "QB", "RB", "WR", "TE"] as PositionTab[]).map((p) => (
            <Button
              key={p}
              size="sm"
              variant={tab === p ? "solid" : "outline"}
              colorScheme={tab === p ? "blue" : "gray"}
              onClick={() => setTab(p)}
            >
              {p === "ALL" ? "Overall" : p}
            </Button>
          ))}
          <Box flex="1" />
          <HStack spacing={2} fontSize="xs" color="gray.600">
            <Box w="10px" h="10px" bg="yellow.100" borderWidth="1px" borderRadius="sm" />
            <Tooltip
              label="DraftKings did not have a Vegas line for this stat. Value is the FantasyPros backup projection."
              hasArrow
            >
              <Text textDecoration="underline" textUnderlineOffset="2px">
                Backup projection
              </Text>
            </Tooltip>
          </HStack>
        </HStack>

        {/* Table */}
        <Box
          borderWidth="1px"
          borderRadius="md"
          bg="white"
          overflow="auto"
          maxH="75vh"
        >
          <HStack justify="space-between" px={3} py={2} bg="gray.50" borderBottomWidth="1px">
            <Text fontWeight="semibold">
              {tab === "ALL" ? "Overall Rankings" : `${tab} Rankings`}
            </Text>
            <Text fontSize="sm" color="gray.600">
              {sortedRows.length} player{sortedRows.length === 1 ? "" : "s"}
            </Text>
          </HStack>

          <Table size="sm" variant="simple" sx={{ borderCollapse: "separate" }}>
            <Thead position="sticky" top={0} bg="gray.100" zIndex={1}>
              <Tr>
                {cols.map((c) => {
                  const active = sortKey === c.key;
                  return (
                    <Th
                      key={c.key}
                      isNumeric={c.numeric}
                      cursor="pointer"
                      onClick={() => handleHeaderClick(c)}
                      userSelect="none"
                      whiteSpace="nowrap"
                    >
                      <HStack
                        spacing={1}
                        justify={c.numeric ? "flex-end" : "flex-start"}
                      >
                        <Text>{c.label}</Text>
                        {active && (sortDir === "asc" ? <TriangleUpIcon /> : <TriangleDownIcon />)}
                      </HStack>
                    </Th>
                  );
                })}
              </Tr>
            </Thead>
            <Tbody>
              {sortedRows.length === 0 ? (
                <Tr>
                  <Td colSpan={cols.length} textAlign="center" py={8} color="gray.500">
                    No players match your filters.
                  </Td>
                </Tr>
              ) : (
                sortedRows.map(({ row: p, rank }) => (
                  <Tr key={p.PID ?? p.NAME} _hover={{ bg: "gray.50" }}>
                    {cols.map((c) => {
                      if (c.key === "NAME") {
                        return (
                          <Td key={c.key} minW="200px">
                            <HStack spacing={3}>
                              {p.PID ? (
                                <Image
                                  src={`https://sleepercdn.com/content/nfl/players/${p.PID}.jpg`}
                                  alt={p.NAME}
                                  boxSize="28px"
                                  objectFit="cover"
                                  borderRadius="full"
                                  fallbackSrc=""
                                />
                              ) : (
                                <Box boxSize="28px" />
                              )}
                              <Box>
                                <Text fontWeight="semibold" noOfLines={1}>
                                  {p.NAME}
                                </Text>
                                {tab === "ALL" && p.POS && (
                                  <Text fontSize="xs" color="gray.500">
                                    {p.POS}
                                  </Text>
                                )}
                              </Box>
                            </HStack>
                          </Td>
                        );
                      }
                      if (c.key === "POS") {
                        return (
                          <Td key={c.key}>
                            <Badge colorScheme={posColor(p.POS)}>{p.POS ?? ""}</Badge>
                          </Td>
                        );
                      }
                      if (c.key === "RANK") {
                        return (
                          <Td key={c.key} isNumeric color="gray.500" fontWeight="medium">
                            {rank}
                          </Td>
                        );
                      }
                      if (c.key === "VEGAS") {
                        const v = p.VEGAS;
                        return (
                          <Td key={c.key} isNumeric fontWeight="semibold">
                            {typeof v === "number" ? v.toFixed(2) : v ?? ""}
                          </Td>
                        );
                      }
                      // Stat column
                      const parsed = parseStat(p.PROJ?.[c.statKey ?? ""] ?? null);
                      return (
                        <Td
                          key={c.key}
                          isNumeric
                          bg={parsed.isBackup ? "yellow.100" : undefined}
                        >
                          {parsed.display}
                        </Td>
                      );
                    })}
                  </Tr>
                ))
              )}
            </Tbody>
          </Table>
        </Box>
      </VStack>
    </Box>
  );
}

function posColor(pos?: string): string {
  switch ((pos ?? "").toUpperCase()) {
    case "QB":
      return "red";
    case "RB":
      return "green";
    case "WR":
      return "blue";
    case "TE":
      return "orange";
    case "K":
      return "purple";
    case "DEF":
    case "DST":
      return "gray";
    default:
      return "gray";
  }
}
