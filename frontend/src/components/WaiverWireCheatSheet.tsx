import React, { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Box,
  Button,
  HStack,
  Image,
  Select,
  Spinner,
  Text,
  Tooltip,
  VStack,
} from "@chakra-ui/react";
import { api } from "../api/client";
import { WaiverWireResponse, WaiverWireRow } from "../types/player";

interface Props {
  /** Active variant from the parent rankings table, so the cheat sheet stays in sync. */
  variant: string;
}

const POSITIONS: ReadonlyArray<string> = ["QB", "RB", "WR", "TE"];

const MAX_OWNED_OPTIONS = [25, 50, 75];

function posColor(pos?: string): string {
  switch ((pos ?? "").toUpperCase()) {
    case "QB": return "red";
    case "RB": return "green";
    case "WR": return "blue";
    case "TE": return "orange";
    default: return "gray";
  }
}

export const WaiverWireCheatSheet: React.FC<Props> = ({ variant }) => {
  const [open, setOpen] = useState(false);
  const [maxOwned, setMaxOwned] = useState(50);
  const [data, setData] = useState<WaiverWireResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Fetch when opened, when variant changes, or when ownership filter changes.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    api
      .getWaiverWire({ variant, maxOwned, topN: 12 })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        console.error("Waiver wire fetch failed", e);
        if (!cancelled) setErr("Couldn't load waiver wire.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, variant, maxOwned]);

  const totalRows = useMemo(() => {
    if (!data) return 0;
    return POSITIONS.reduce((acc, p) => acc + (data.by_position[p]?.length ?? 0), 0);
  }, [data]);

  return (
    <Box borderWidth="1px" borderRadius="md" bg="white" px={3} py={2}>
      <HStack justify="space-between" wrap="wrap" gap={3}>
        <HStack gap={2}>
          <Button
            size="sm"
            colorScheme={open ? "blue" : "gray"}
            variant={open ? "solid" : "outline"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "▾ Hide" : "▸ Show"} Waiver Wire Cheat Sheet
          </Button>
          <Tooltip
            hasArrow
            label="Top low-owned players per position by Vegas points. Driven by Sleeper league-wide ownership data — useful even if you haven't loaded a username."
          >
            <Text fontSize="xs" color="gray.500" textDecoration="underline" textUnderlineOffset="2px">
              what is this?
            </Text>
          </Tooltip>
        </HStack>
        {open && (
          <HStack gap={2}>
            <Text fontSize="sm">Max ownership:</Text>
            <Select
              size="sm"
              width="100px"
              value={maxOwned}
              onChange={(e) => setMaxOwned(Number(e.target.value))}
            >
              {MAX_OWNED_OPTIONS.map((p) => (
                <option key={p} value={p}>
                  &lt; {p}%
                </option>
              ))}
            </Select>
          </HStack>
        )}
      </HStack>

      {open && (
        <Box mt={3}>
          {loading && (
            <HStack py={4}>
              <Spinner size="sm" />
              <Text fontSize="sm" color="gray.600">Loading top free agents…</Text>
            </HStack>
          )}
          {err && <Text color="red.500" fontSize="sm">{err}</Text>}
          {!loading && !err && data && totalRows === 0 && (
            <Text fontSize="sm" color="gray.500" py={4}>
              No projected players currently fit the &lt; {maxOwned}% ownership threshold for this scoring variant.
            </Text>
          )}
          {!loading && !err && data && totalRows > 0 && (
            <HStack align="flex-start" gap={4} overflowX="auto" pb={2}>
              {POSITIONS.map((pos) => {
                const rows = data.by_position[pos] ?? [];
                if (rows.length === 0) return null;
                return (
                  <Box
                    key={pos}
                    flex="1 1 220px"
                    minW="220px"
                    borderWidth="1px"
                    borderRadius="md"
                    overflow="hidden"
                  >
                    <HStack
                      px={2}
                      py={1}
                      bg="gray.50"
                      borderBottomWidth="1px"
                      justify="space-between"
                    >
                      <Badge colorScheme={posColor(pos)}>{pos}</Badge>
                      <Text fontSize="xs" color="gray.600">
                        {rows.length} player{rows.length === 1 ? "" : "s"}
                      </Text>
                    </HStack>
                    <VStack align="stretch" gap={0} divider={undefined}>
                      {rows.map((p, i) => (
                        <CheatSheetRow key={p.PID ?? p.NAME} row={p} rank={i + 1} />
                      ))}
                    </VStack>
                  </Box>
                );
              })}
            </HStack>
          )}
        </Box>
      )}
    </Box>
  );
};

interface RowProps {
  row: WaiverWireRow;
  rank: number;
}

const CheatSheetRow: React.FC<RowProps> = ({ row, rank }) => {
  const headshot = row.PID
    ? `https://sleepercdn.com/content/nfl/players/${row.PID}.jpg`
    : undefined;
  const ceiling = typeof row.P90 === "number" ? row.P90 : null;
  const floor = typeof row.P10 === "number" ? row.P10 : null;
  const boom = typeof row.BOOM === "number" ? row.BOOM : null;

  return (
    <HStack px={2} py={1} borderTopWidth={rank === 1 ? "0" : "1px"} gap={2} align="center">
      <Text fontSize="xs" color="gray.500" w="18px">
        {rank}
      </Text>
      {headshot ? (
        <Image
          src={headshot}
          alt={row.NAME}
          boxSize="28px"
          objectFit="cover"
          borderRadius="full"
          fallbackSrc=""
        />
      ) : (
        <Box boxSize="28px" />
      )}
      <Box flex="1" minW={0}>
        <Text fontSize="sm" fontWeight="semibold" noOfLines={1}>
          {row.NAME}
        </Text>
        <HStack gap={2} fontSize="2xs" color="gray.600">
          <Text>
            <b>{row.VEGAS != null ? Number(row.VEGAS).toFixed(1) : "–"}</b> pts
          </Text>
          {ceiling !== null && floor !== null && (
            <Text>
              ({floor.toFixed(0)}–{ceiling.toFixed(0)})
            </Text>
          )}
          {boom !== null && <Text>boom {(boom * 100).toFixed(0)}%</Text>}
        </HStack>
      </Box>
      <Tooltip label={`${row.OWNED_PCT ?? 0}% rostered across Sleeper leagues`} hasArrow>
        <Badge colorScheme={(row.OWNED_PCT ?? 0) < 10 ? "purple" : "gray"} fontSize="2xs">
          {Math.round(row.OWNED_PCT ?? 0)}%
        </Badge>
      </Tooltip>
    </HStack>
  );
};

export default WaiverWireCheatSheet;
