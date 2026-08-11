import React from 'react';
import { Box, Image } from '@chakra-ui/react';

interface DraftPlayerAvatarProps {
  playerId: string;
  name: string;
  team?: string | null;
  size?: number;
}

/** Sleeper-CDN headshot with a small team-logo overlay; no API lookup needed. */
const DraftPlayerAvatar: React.FC<DraftPlayerAvatarProps> = ({
  playerId,
  name,
  team,
  size = 34,
}) => {
  const teamCode = (team || '').toLowerCase();
  return (
    <Box position="relative" boxSize={`${size}px`} flexShrink={0} bg="gray.100" borderRadius="full">
      <Image
        src={`https://sleepercdn.com/content/nfl/players/${playerId}.jpg`}
        alt={name}
        boxSize={`${size}px`}
        objectFit="cover"
        borderRadius="full"
        bg="gray.100"
        onError={(event) => { event.currentTarget.style.display = 'none'; }}
        loading="lazy"
      />
      {teamCode && (
        <Image
          src={`https://sleepercdn.com/images/team_logos/nfl/${teamCode}.png`}
          alt={`${team} logo`}
          boxSize={`${Math.max(14, Math.round(size * 0.45))}px`}
          objectFit="contain"
          position="absolute"
          right="-2px"
          bottom="-2px"
          bg="white"
          borderRadius="full"
          p="1px"
          onError={(event) => { event.currentTarget.style.display = 'none'; }}
          loading="lazy"
        />
      )}
    </Box>
  );
};

export default DraftPlayerAvatar;
