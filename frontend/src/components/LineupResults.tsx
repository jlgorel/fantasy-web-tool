import React, { useMemo, useState } from 'react';
import { Box, Button, ButtonGroup, HStack, Spinner, Text } from '@chakra-ui/react';

import LineupConfidence from './LineupConfidence';
import PlayerTable from './PlayerTable';
import { FreeAgentRecs, Player } from '../types/player';

type LineupView = 'boris' | 'vegas' | 'your';

interface LineupResultsProps {
  borisOptimized: Player[];
  vegasOptimized?: Player[] | null;
  yourLineup?: Player[] | null;
  freeAgentRecs?: FreeAgentRecs;
  refreshing?: boolean;
  loadingLabel?: string;
  freeAgentNotice?: string;
}

const LineupResults: React.FC<LineupResultsProps> = ({
  borisOptimized,
  vegasOptimized,
  yourLineup,
  freeAgentRecs,
  refreshing = false,
  loadingLabel = 'lineup',
  freeAgentNotice,
}) => {
  const [lineupView, setLineupView] = useState<LineupView>('boris');
  const displayedLineup = useMemo(() => {
    if (lineupView === 'vegas') return vegasOptimized ?? borisOptimized;
    if (lineupView === 'your') return yourLineup ?? borisOptimized;
    return borisOptimized;
  }, [lineupView, borisOptimized, vegasOptimized, yourLineup]);

  const hasYourLineup = !!yourLineup;
  const hasVegasLineup = !!vegasOptimized;

  return (
    <Box position="relative" opacity={refreshing ? 0.55 : 1} transition="opacity 0.15s">
      {(hasYourLineup || hasVegasLineup) && (
        <HStack justify="center" mb={2}>
          <ButtonGroup size="sm" isAttached variant="outline">
            <Button
              onClick={() => setLineupView('boris')}
              colorScheme={lineupView === 'boris' ? 'blue' : 'gray'}
              variant={lineupView === 'boris' ? 'solid' : 'outline'}
            >
              Boris-Optimized
            </Button>
            {hasVegasLineup && (
              <Button
                onClick={() => setLineupView('vegas')}
                colorScheme={lineupView === 'vegas' ? 'blue' : 'gray'}
                variant={lineupView === 'vegas' ? 'solid' : 'outline'}
              >
                Projected-Points Optimized
              </Button>
            )}
            {hasYourLineup && (
              <Button
                onClick={() => setLineupView('your')}
                colorScheme={lineupView === 'your' ? 'blue' : 'gray'}
                variant={lineupView === 'your' ? 'solid' : 'outline'}
              >
                Your Lineup
              </Button>
            )}
          </ButtonGroup>
        </HStack>
      )}

      {freeAgentNotice && (
        <Text fontSize="sm" color="gray.600" textAlign="center" mb={3}>
          {freeAgentNotice}
        </Text>
      )}
      <LineupConfidence starters={displayedLineup} />
      <PlayerTable data={displayedLineup} freeAgentRecs={freeAgentRecs} />
      {refreshing && (
        <Box
          position="absolute"
          top={4}
          right={4}
          bg="white"
          borderWidth="1px"
          borderRadius="md"
          px={3}
          py={2}
          boxShadow="md"
          zIndex={2}
        >
          <HStack gap={2}>
            <Spinner size="sm" />
            <Text fontSize="sm">Loading {loadingLabel}...</Text>
          </HStack>
        </Box>
      )}
    </Box>
  );
};

export default LineupResults;