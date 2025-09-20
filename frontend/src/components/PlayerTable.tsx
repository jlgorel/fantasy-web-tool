import React, { useState } from 'react';
import {
  Box,
  Text,
  Image,
  SimpleGrid,
  Grid,
  HStack,
  VStack,
  useBreakpointValue,
  Collapse,
  Slider,
  SliderTrack,
  SliderFilledTrack,
  SliderThumb,
  Tooltip
} from '@chakra-ui/react';

interface Player {
  NAME: string;
  POS: string;              
  POS_RANK?: string;        
  FLEX?: string;            
  PID?: string;             
  TEAM?: string;            
  TEAM_NAME?: string;       
  VEGAS?: string;           
  MATCHUP_RATING?: string;  
  REALLIFE_POS?: string;    
  VEGAS_STATS?: string;     
  BOOM?: string;            
  BUST?: string;
  PERCENTILES?: string;   
}

interface PlayerTableProps {
  data: Player[];
  freeAgentRecs?: { [position: string]: Player[] }; 
}

// Tier colors
const tierColors: { [key: string]: string } = {
  '1': '#004d00',
  '2': '#006400',
  '3': '#4CAF50',
  '4': '#8BC34A',
  '5': '#FFC107',
  '6': '#FFB74D',
  '7': '#FF9800',
  '8': '#FF5722',
  '9': '#F44336',
  '10': '#B71C1C',
};

const getColorForRank = (rank?: string) => {
  if (!rank) return '#B71C1C';
  const val = parseInt(rank, 10);
  return tierColors[val?.toString()] ?? '#B71C1C';
};

// Grid template
const useTemplateColumns = () =>
  useBreakpointValue({
    base: '40px minmax(150px, 1.5fr) 0.5fr 0.5fr 0.5fr',
    md: '40px minmax(220px, 2.5fr) 1fr 1fr 1fr',
  });

interface PlayerRowProps {
  p: Player;
  isStarter: boolean;
  templateCols: string;
  expanded: boolean;
  onToggle: () => void;
}

const PlayerRow: React.FC<PlayerRowProps> = ({
  p,
  isStarter,
  templateCols,
  expanded,
  onToggle,
}) => {
  const [threshold, setThreshold] = useState(10);

  // Use percentiles directly, keys might be strings, convert to numbers safely
  const percentiles: Record<number, number> | null =
    p.PERCENTILES && p.PERCENTILES !== 'N/A'
      ? Object.fromEntries(
          Object.entries(p.PERCENTILES).map(([k, v]) => [Number(k), Number(v)])
        )
      : null;

  // Compute probability of scoring at least threshold
  let probAtLeast = 0;
  if (percentiles) {
    const sortedValues = Object.entries(percentiles)
      .map(([k, v]) => [Number(k), Number(v)] as [number, number])
      .sort((a, b) => a[1] - b[1]);

    const below = sortedValues.filter(([, val]) => val < threshold);
    const percentileBelow = below.length > 0 ? below[below.length - 1][0] : 0;
    probAtLeast = (100 - percentileBelow) / 100;
  }

  const displayPos =
    p.POS === 'REC_FLEX'
      ? 'W/T'
      : p.POS === 'SUPER_FLEX'
      ? 'SF'
      : p.POS === 'FLEX'
      ? 'W/R/T'
      : p.POS;

  const showFlex =
    p.POS !== 'REC_FLEX' &&
    p.REALLIFE_POS !== 'QB' &&
    p.POS !== 'K' &&
    p.POS !== 'DEF' &&
    p.FLEX;

  const teamCode = (p.TEAM_NAME ?? p.TEAM)?.toLowerCase();
  const teamLogo = teamCode
    ? `https://sleepercdn.com/images/team_logos/nfl/${teamCode}.png`
    : undefined;
  const headshot = p.PID
    ? `https://sleepercdn.com/content/nfl/players/${p.PID}.jpg`
    : undefined;

  const rowFont = useBreakpointValue({ base: 'xs', md: 'sm' });
  const posFont = useBreakpointValue({ base: '2xs', md: 'xs' });

  return (
    <Box
      borderWidth="1px"
      borderRadius="md"
      mb={2}
      bg={isStarter ? 'gray.50' : 'white'}
      _hover={{ transform: 'translateY(-2px)', boxShadow: 'lg' }}
      transition="all 0.2s"
      cursor="pointer"
      onClick={onToggle} // controlled from parent
    >
      <Grid templateColumns={templateCols} alignItems="center" gap={2} p={2}>
        {/* Position */}
        <Text fontWeight="bold" fontSize={posFont}>
          {displayPos}
        </Text>

        {/* Player image + name */}
        <Box minW={0}>
          <Grid templateColumns="50px 1fr" gap={2} alignItems="center">
            <Box position="relative" boxSize="50px" flex="0 0 auto">
              {p.POS === 'DEF' && teamLogo ? (
                <Image
                  src={teamLogo}
                  alt={teamCode}
                  w="100%"
                  h="100%"
                  objectFit="contain"
                  borderRadius="md"
                  fallbackSrc=""
                />
              ) : (
                <>
                  {headshot && (
                    <Image
                      src={headshot}
                      alt={p.NAME}
                      w="100%"
                      h="100%"
                      objectFit="cover"
                      borderRadius="full"
                      fallbackSrc=""
                    />
                  )}
                  {teamLogo && (
                    <Image
                      src={teamLogo}
                      alt={teamCode}
                      boxSize="20px"
                      position="absolute"
                      bottom="0"
                      right="0"
                      fallbackSrc=""
                    />
                  )}
                </>
              )}
            </Box>
            <Text fontWeight="semibold" fontSize={rowFont} noOfLines={1} minW={0}>
              {p.NAME}
            </Text>
          </Grid>
        </Box>

        {/* Tier info */}
        <VStack gap={1} fontSize={rowFont} justify="flex-start" align="flex-start">
          <HStack gap={1}>
            <Box w="10px" h="10px" borderRadius="50%" bg={getColorForRank(p.POS_RANK)} />
            <Text noOfLines={1}>
              {p.REALLIFE_POS || 'DEF'} {p.POS_RANK}
            </Text>
          </HStack>
          {showFlex && (
            <HStack gap={1}>
              <Box w="10px" h="10px" borderRadius="50%" bg={getColorForRank(p.FLEX)} />
              <Text noOfLines={1}>Flex {p.FLEX}</Text>
            </HStack>
          )}
        </VStack>

        {/* Vegas points */}
        <HStack gap={1} justify="flex-start" fontSize={rowFont}>
          <Text whiteSpace="nowrap">
            {(p.VEGAS ?? '0') + (p.VEGAS !== 'N/A' ? ' pts' : '')}
          </Text>
        </HStack>

        {/* Matchup stars */}
        <HStack gap={1} justify="flex-start" fontSize={rowFont}>
          <Text>
            {Array.from({ length: parseInt(p.MATCHUP_RATING ?? '0', 10) || 0 })
              .map(() => '⭐')
              .join('')}
          </Text>
        </HStack>
      </Grid>

      {/* Expanded section for stats */}
      <Collapse in={expanded} animateOpacity>
        {p.VEGAS_STATS && (
          <Box
            p={4}
            bg="gray.50"
            borderTopWidth="1px"
            borderRadius="md"
            mt={2}
          >
            <Text fontSize={rowFont}>Predicted statline: {p.VEGAS_STATS}</Text>
            {/* Boom/Bust bars */}
            <HStack spacing={6} fontSize="sm" mb={4}>
              {/* Boom */}
              <VStack align="start" spacing={1} w="100px">
                <Text fontSize="xs" fontWeight="bold" color="green.700">
                  Boom
                </Text>
                <Box w="100%" bg="green.100" borderRadius="md" h="12px">
                  <Box
                    bg="green.500"
                    h="100%"
                    borderRadius="md"
                    width={`${p.BOOM ?? 0}%`}
                    transition="width 0.2s ease"
                  />
                </Box>
                <Text fontSize="xs" color="green.700">
                  {p.BOOM ?? 0}%
                </Text>
              </VStack>

              {/* Bust */}
              <VStack align="start" spacing={1} w="100px">
                <Text fontSize="xs" fontWeight="bold" color="red.700">
                  Bust
                </Text>
                <Box w="100%" bg="red.100" borderRadius="md" h="12px">
                  <Box
                    bg="red.500"
                    h="100%"
                    borderRadius="md"
                    width={`${p.BUST ?? 0}%`}
                    transition="width 0.2s ease"
                  />
                </Box>
                <Text fontSize="xs" color="red.700">
                  {p.BUST ?? 0}%
                </Text>
              </VStack>
            </HStack>

            {percentiles ? (
              <Box>
                {/* Slider with Tooltip */}
                <Tooltip
                  label={`${Math.round(probAtLeast * 100)}% ≥ ${threshold} pts`}
                  placement="top"
                  hasArrow
                >
                  <Box>
                    <Text fontSize="xs" fontWeight="semibold" mb={2}>
                      Minimum Point Threshold: {threshold} pts
                    </Text>
                    <Slider
                      aria-label="threshold-slider"
                      min={0}
                      max={Math.ceil(percentiles[100] ?? 50)}
                      value={threshold}
                      onChange={setThreshold}
                      mb={4}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <SliderTrack bg="gray.300" borderRadius="md">
                        <SliderFilledTrack bg="blue.400" />
                      </SliderTrack>
                      <SliderThumb boxSize={4} />
                    </Slider>
                  </Box>
                </Tooltip>

                {/* Threshold bar */}
                <Text fontSize="sm" mb={1}>
                  {Math.round(probAtLeast * 100)}% of simulations ≥ {threshold} pts
                </Text>
                <Box w="100%" bg="red.100" borderRadius="md" h="12px">
                  <Box
                    bg="green.400"
                    h="100%"
                    borderRadius="md"
                    width={`${probAtLeast * 100}%`}
                    transition="width 0.2s ease"
                  />
                </Box>
              </Box>
            ) : (
              <Text fontSize="sm" color="gray.500" mt={2}>
                Percentiles unavailable
              </Text>
            )}
          </Box>
        )}
      </Collapse>
    </Box>
  );
};

const PlayerTable: React.FC<PlayerTableProps> = ({ data, freeAgentRecs }) => {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const starters = data.filter((p) => p.POS !== 'BN');
  const bench = data.filter((p) => p.POS === 'BN');
  const templateCols = useTemplateColumns();

  const handleToggle = (id: string) => {
    setExpandedRow(expandedRow === id ? null : id);
  };

  return (
    <Box w="100%" p={4}>
      <SimpleGrid minChildWidth="460px" gap={6} alignItems="flex-start">
        {/* Starters Section */}
        <Box>
          <Text fontSize="lg" fontWeight="bold" mb={2}>
            Starters
          </Text>
          <Grid templateColumns={templateCols} gap={2} mb={1}>
            <Text fontWeight="bold" fontSize="sm" ml={1}>Pos</Text>
            <Text fontWeight="bold" fontSize="sm" ml={3}>Player</Text>
            <Text fontWeight="bold" fontSize="sm">Boris Tiers</Text>
            <Text fontWeight="bold" fontSize="sm">Vegas Points</Text>
            <Text fontWeight="bold" fontSize="sm">Matchup</Text>
          </Grid>
          {starters.map((p, i) => (
            <PlayerRow
              key={`${p.PID ?? p.NAME}-starter-${i}`}
              p={p}
              isStarter
              templateCols={templateCols!}
              expanded={expandedRow === (p.PID ?? p.NAME)}
              onToggle={() => handleToggle(p.PID ?? p.NAME)}
            />
          ))}
        </Box>

        {/* Bench Section */}
        <Box>
          <Text fontSize="lg" fontWeight="bold" mb={2}>
            Bench
          </Text>
          <Grid templateColumns={templateCols} gap={2} mb={1}>
            <Text fontWeight="bold" fontSize="sm" ml={1}>Pos</Text>
            <Text fontWeight="bold" fontSize="sm" ml={3}>Player</Text>
            <Text fontWeight="bold" fontSize="sm">Boris Tiers</Text>
            <Text fontWeight="bold" fontSize="sm">Vegas Points</Text>
            <Text fontWeight="bold" fontSize="sm">Matchup</Text>
          </Grid>
          {bench.map((p, i) => (
            <PlayerRow
              key={`${p.PID ?? p.NAME}-bench-${i}`}
              p={p}
              isStarter={false}
              templateCols={templateCols!}
              expanded={expandedRow === (p.PID ?? p.NAME)}
              onToggle={() => handleToggle(p.PID ?? p.NAME)}
            />
          ))}
        </Box>

        {/* Free Agent Section */}
        {freeAgentRecs && (
        <Box>
          <Text fontSize="lg" fontWeight="bold" mb={2}>
            Top Free Agents
          </Text>
          {Object.keys(freeAgentRecs).map((pos) => (
            <Box key={`fa-${pos}`} mb={4}>
              <Text fontSize="md" fontWeight="semibold" mb={1}>{pos}</Text>
              <Grid templateColumns={templateCols} gap={2} mb={1}>
                <Text fontWeight="bold" fontSize="sm" ml={1}>Pos</Text>
                <Text fontWeight="bold" fontSize="sm" ml={3}>Player</Text>
                <Text fontWeight="bold" fontSize="sm">Boris Tiers</Text>
                <Text fontWeight="bold" fontSize="sm">Vegas Points</Text>
                <Text fontWeight="bold" fontSize="sm">Matchup</Text>
              </Grid>
              {freeAgentRecs[pos] && Array.isArray(freeAgentRecs[pos]) && freeAgentRecs[pos].map((p, i) => (
                <PlayerRow
                  key={`${p.PID ?? p.NAME}-fa-${pos}-${i}`}
                  p={p}
                  isStarter={false}
                  templateCols={templateCols!}
                  expanded={expandedRow === (p.PID ?? p.NAME)}
                  onToggle={() => handleToggle(p.PID ?? p.NAME)}
                />
              ))}
            </Box>
          ))}
        </Box>
      )}
      </SimpleGrid>
    </Box>
  );
};

export default PlayerTable;
