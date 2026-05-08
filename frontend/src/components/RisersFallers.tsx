import React, { useEffect, useState } from "react";
import {
  Badge,
  Box,
  Button,
  HStack,
  Image,
  Spinner,
  Text,
  Tooltip,
  VStack,
} from "@chakra-ui/react";
import { TriangleDownIcon, TriangleUpIcon } from "@chakra-ui/icons";
import { api } from "../api/client";
import { MoverRow, RisersFallersResponse } from "../types/player";

interface Props {
  variant: string;
}

function posColor(pos?: string): string {
  switch ((pos ?? "").toUpperCase()) {
    case "QB": return "red";
    case "RB": return "green";
    case "WR": return "blue";
    case "TE": return "orange";
    default: return "gray";
  }
}

const RisersFallers: React.FC<Props> = ({ variant }) => {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<RisersFallersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    api
      .getRisersFallers({ variant, topN: 10 })
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e) => {
        console.error("Risers/fallers fetch failed", e);
        if (!cancelled) setErr("Couldn't load risers / fallers.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, variant]);

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
            {open ? "▾ Hide" : "▸ Show"} Top Risers / Fallers
          </Button>
          <Tooltip
            hasArrow
            label="Biggest projected-point movers since the previous scrape (Vegas line shifts, news, lineup changes). Only updates when new lines drop."
          >
            <Text fontSize="xs" color="gray.500" textDecoration="underline" textUnderlineOffset="2px">
              what is this?
            </Text>
          </Tooltip>
        </HStack>
      </HStack>

      {open && (
        <Box mt={3}>
          {loading && (
            <HStack py={4}>
              <Spinner size="sm" />
              <Text fontSize="sm" color="gray.600">Loading movers…</Text>
            </HStack>
          )}
          {err && <Text color="red.500" fontSize="sm">{err}</Text>}
          {!loading && !err && data && !data.available && (
            <Text fontSize="sm" color="gray.500" py={3}>
              {data.message ?? "No previous-run snapshot available yet."}
            </Text>
          )}
          {!loading && !err && data && data.available && (
            <HStack align="flex-start" gap={4} flexWrap="wrap">
              <MoverColumn title="🔥 Risers" rows={data.risers} mode="riser" />
              <MoverColumn title="📉 Fallers" rows={data.fallers} mode="faller" />
            </HStack>
          )}
        </Box>
      )}
    </Box>
  );
};

interface ColumnProps {
  title: string;
  rows: MoverRow[];
  mode: "riser" | "faller";
}

const MoverColumn: React.FC<ColumnProps> = ({ title, rows, mode }) => (
  <Box flex="1 1 320px" minW="320px" borderWidth="1px" borderRadius="md" overflow="hidden">
    <Box px={3} py={1.5} bg="gray.50" borderBottomWidth="1px">
      <Text fontWeight="semibold" fontSize="sm">{title}</Text>
    </Box>
    {rows.length === 0 ? (
      <Text px={3} py={3} fontSize="sm" color="gray.500">
        No notable {mode === "riser" ? "risers" : "fallers"}.
      </Text>
    ) : (
      <VStack align="stretch" gap={0}>
        {rows.map((r, i) => (
          <MoverRowView key={r.PID} row={r} rank={i + 1} mode={mode} />
        ))}
      </VStack>
    )}
  </Box>
);

const MoverRowView: React.FC<{ row: MoverRow; rank: number; mode: "riser" | "faller" }> = ({
  row,
  rank,
  mode,
}) => {
  const headshot = row.PID
    ? `https://sleepercdn.com/content/nfl/players/${row.PID}.jpg`
    : undefined;
  const positive = row.DELTA > 0;
  const Icon = positive ? TriangleUpIcon : TriangleDownIcon;
  const color = positive ? "green.600" : "red.600";

  return (
    <HStack
      px={3}
      py={1.5}
      borderTopWidth={rank === 1 ? "0" : "1px"}
      gap={3}
      align="center"
    >
      <Text fontSize="xs" color="gray.500" w="18px">
        {rank}
      </Text>
      {headshot ? (
        <Image
          src={headshot}
          alt={row.NAME}
          boxSize="32px"
          objectFit="cover"
          borderRadius="full"
          fallbackSrc=""
        />
      ) : (
        <Box boxSize="32px" />
      )}
      <Box flex="1" minW={0}>
        <HStack gap={2}>
          <Text fontSize="sm" fontWeight="semibold" noOfLines={1}>
            {row.NAME}
          </Text>
          {row.POS && <Badge colorScheme={posColor(row.POS)} fontSize="2xs">{row.POS}</Badge>}
        </HStack>
        <Text fontSize="xs" color="gray.600">
          {row.PREV_VEGAS.toFixed(1)} → <b>{row.VEGAS.toFixed(1)}</b> pts
        </Text>
      </Box>
      <VStack align="flex-end" gap={0}>
        <HStack gap={1} color={color}>
          <Icon boxSize="10px" />
          <Text fontSize="sm" fontWeight="bold">
            {positive ? "+" : ""}
            {row.DELTA.toFixed(2)}
          </Text>
        </HStack>
        {row.DELTA_PCT !== null && (
          <Text fontSize="2xs" color="gray.500">
            {positive ? "+" : ""}
            {row.DELTA_PCT.toFixed(1)}%
          </Text>
        )}
      </VStack>
    </HStack>
  );
};

export default RisersFallers;
