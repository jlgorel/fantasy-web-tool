import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Badge,
  Box,
  Button,
  Input,
  Portal,
  VStack,
} from '@chakra-ui/react';
import { RankingsPlayerRow } from '../types/draft';

interface PlayerComboboxProps {
  players: RankingsPlayerRow[];
  value: string;
  onChange: (playerId: string) => void;
  placeholder?: string;
}

const PlayerCombobox: React.FC<PlayerComboboxProps> = ({
  players,
  value,
  onChange,
  placeholder = 'Search and select player…',
}) => {
  const selected = players.find((player) => player.player_id === value);
  const [query, setQuery] = useState(selected?.name || '');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const anchorRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();
  const [menuPosition, setMenuPosition] = useState<{
    left: number;
    top: number;
    width: number;
    maxHeight: number;
  } | null>(null);

  useEffect(() => {
    setQuery(selected?.name || '');
  }, [selected?.name]);

  const matches = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return players
      .filter((player) => !normalized
        || player.name.toLowerCase().includes(normalized)
        || player.pos.toLowerCase().includes(normalized))
      .sort((a, b) => a.name.localeCompare(b.name))
      .slice(0, 12);
  }, [players, query]);

  const select = (player: RankingsPlayerRow) => {
    setQuery(player.name);
    onChange(player.player_id);
    setOpen(false);
  };

  const positionMenu = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const spaceBelow = Math.max(120, window.innerHeight - rect.bottom - 12);
    setMenuPosition({
      left: rect.left,
      top: rect.bottom + 4,
      width: rect.width,
      maxHeight: Math.min(260, spaceBelow),
    });
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    positionMenu();
    window.addEventListener('resize', positionMenu);
    window.addEventListener('scroll', positionMenu, true);
    return () => {
      window.removeEventListener('resize', positionMenu);
      window.removeEventListener('scroll', positionMenu, true);
    };
  }, [open, positionMenu]);

  return (
    <Box ref={anchorRef} position="relative" minW="240px" maxW="320px" flex="1">
      <Input
        size="sm"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        value={query}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => {
          setQuery(event.target.value);
          onChange('');
          setOpen(true);
          setActiveIndex(0);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
            setActiveIndex((index) => Math.min(index + 1, matches.length - 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActiveIndex((index) => Math.max(index - 1, 0));
          } else if (event.key === 'Enter' && open && matches[activeIndex]) {
            event.preventDefault();
            select(matches[activeIndex]);
          } else if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
      />
      {open && menuPosition && (
        <Portal>
          <VStack
            id={listboxId}
            role="listbox"
            data-testid="player-combobox-options"
            align="stretch"
            spacing={0}
            position="fixed"
            left={`${menuPosition.left}px`}
            top={`${menuPosition.top}px`}
            width={`${menuPosition.width}px`}
            maxH={`${menuPosition.maxHeight}px`}
            overflowY="auto"
            bg="white"
            borderWidth="1px"
            borderRadius="md"
            boxShadow="lg"
            zIndex={1500}
          >
            {matches.map((player, index) => (
              <Button
                key={player.player_id}
                role="option"
                aria-selected={player.player_id === value}
                size="sm"
                variant="ghost"
                borderRadius={0}
                justifyContent="space-between"
                flexShrink={0}
                bg={index === activeIndex ? 'blue.50' : undefined}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(player)}
              >
                <span>{player.name}</span>
                <Badge ml={2}>{player.pos}</Badge>
              </Button>
            ))}
            {!matches.length && (
              <Box px={3} py={2} fontSize="sm" color="gray.500">
                No matching players
              </Box>
            )}
          </VStack>
        </Portal>
      )}
    </Box>
  );
};

export default PlayerCombobox;
