/**
 * Single-trade inspector panel for **redraft** leagues.
 *
 * Renders inline in the same Wrapped trade ledger row that the dynasty
 * `TradeInspector` uses, but scores the trade with rest-of-season VORP
 * instead of KTC value-integration. Picks aren't relevant here -- they
 * aren't traded in redraft once the draft has happened.
 *
 * What you see:
 *
 *   1. A headline verdict ("Alice won by 47 VORP" / "Wash") whose color
 *      matches the margin label (decisive / close / wash).
 *
 *   2. A side-by-side breakdown: for each acquired player, ROS points,
 *      games played in the window, PPG, and VORP. The 3-for-1 trap is
 *      visible at a glance -- waiver-tier fillers light up as ~0 VORP.
 *
 * Loading + error rendering lives in this component so the parent just
 * has to toggle the expanded transaction id.
 */
import React, { useEffect, useState } from 'react';
import {
  Box,
  Heading,
  HStack,
  VStack,
  Text,
  Spinner,
  Tag,
  Table,
  Thead,
  Tbody,
  Tr,
  Th,
  Td,
  TableContainer,
  Divider,
} from '@chakra-ui/react';
import { api } from '../api/client';
import {
  WrappedInspectRedraftTrade,
  WrappedRedraftSide,
} from '../types/player';

interface TradeInspectorRedraftProps {
  leagueId: string;
  transactionId: string;
  year: string;
}

function verdictColor(marginLabel: string): string {
  if (marginLabel === 'decisive') return 'red';
  if (marginLabel === 'close') return 'orange';
  return 'gray';
}

function formatVorp(v: number): string {
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(1)}`;
}

function SideTable({ side }: { side: WrappedRedraftSide }) {
  return (
    <Box flex={1} minW={0}>
      <HStack justify="space-between" mb={1}>
        <Text fontWeight="semibold" fontSize="sm">
          {side.username}
        </Text>
        <Text fontSize="xs" color="gray.600">
          Total VORP:{' '}
          <Text as="span" fontWeight="semibold">
            {formatVorp(side.total_vorp)}
          </Text>
        </Text>
      </HStack>
      <TableContainer>
        <Table
          size="sm"
          variant="simple"
          sx={{ 'th, td': { px: 2, py: 1, fontSize: '2xs' } }}
        >
          <Thead>
            <Tr>
              <Th>Player</Th>
              <Th>Pos</Th>
              <Th isNumeric>G</Th>
              <Th isNumeric>PPG</Th>
              <Th isNumeric>Pts</Th>
              <Th isNumeric>VORP</Th>
            </Tr>
          </Thead>
          <Tbody>
            {side.assets.length === 0 && (
              <Tr>
                <Td colSpan={6}>
                  <Text fontSize="2xs" color="gray.500" fontStyle="italic">
                    No players acquired.
                  </Text>
                </Td>
              </Tr>
            )}
            {side.assets.map((a) => (
              <Tr key={a.player_id}>
                <Td>{a.name}</Td>
                <Td>{a.position}</Td>
                <Td isNumeric>{a.games_played}</Td>
                <Td isNumeric>{a.ros_ppg.toFixed(1)}</Td>
                <Td isNumeric>{a.ros_points.toFixed(0)}</Td>
                <Td
                  isNumeric
                  color={
                    a.vorp > 5
                      ? 'green.600'
                      : a.vorp < -5
                        ? 'red.600'
                        : 'gray.700'
                  }
                  fontWeight={Math.abs(a.vorp) > 20 ? 'semibold' : 'normal'}
                >
                  {formatVorp(a.vorp)}
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </TableContainer>
    </Box>
  );
}

export const TradeInspectorRedraft: React.FC<TradeInspectorRedraftProps> = ({
  leagueId,
  transactionId,
  year,
}) => {
  const [data, setData] = useState<WrappedInspectRedraftTrade | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getWrappedInspectTradeRedraft(leagueId, transactionId, year)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message =
            err instanceof Error ? err.message : 'Failed to load trade';
          setError(message);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [leagueId, transactionId, year]);

  if (loading) {
    return (
      <Box py={4} textAlign="center">
        <Spinner size="sm" />
        <Text fontSize="xs" color="gray.500" mt={1}>
          Scoring trade…
        </Text>
      </Box>
    );
  }

  if (error || !data) {
    return (
      <Box py={3}>
        <Text fontSize="xs" color="red.600">
          Couldn't score this trade.
          {error ? ` (${error})` : ''}
        </Text>
      </Box>
    );
  }

  const { evaluation } = data;
  const verdictText =
    evaluation.verdict === 'wash'
      ? 'Wash'
      : `${evaluation.verdict} won by ${formatVorp(evaluation.margin_vorp)} VORP`;

  return (
    <VStack align="stretch" spacing={3}>
      <HStack justify="space-between" wrap="wrap">
        <Heading size="xs" color="gray.700">
          Retrospective verdict
        </Heading>
        <HStack spacing={2}>
          <Tag size="sm" colorScheme={verdictColor(evaluation.margin_label)}>
            {evaluation.margin_label.toUpperCase()}
          </Tag>
          <Text fontSize="sm" fontWeight="semibold">
            {verdictText}
          </Text>
        </HStack>
      </HStack>

      <Text fontSize="2xs" color="gray.500">
        Scored on regular-season weeks{' '}
        <strong>
          {evaluation.window.start_week}–{evaluation.window.end_week}
        </strong>{' '}
        of {evaluation.window.season}, using each league's actual scoring
        ({evaluation.scoring.skill_score_key.replace('_', ' ')}). VORP =
        points scored over the replacement-level player at each position;
        a 3-for-1 wins only when the three pieces stack VORP above the
        headliner.
      </Text>

      <Divider />

      <HStack
        align="flex-start"
        spacing={4}
        wrap={{ base: 'wrap', md: 'nowrap' }}
      >
        {evaluation.sides.map((side) => (
          <SideTable key={side.username} side={side} />
        ))}
      </HStack>
    </VStack>
  );
};
