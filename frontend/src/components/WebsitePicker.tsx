import React from 'react';
import {
  Button,
  HStack,
  Image,
  Menu,
  MenuButton,
  MenuItem,
  MenuList,
  Text,
} from '@chakra-ui/react';
import { ChevronDownIcon } from '@chakra-ui/icons';
import { WebsiteName } from '../types/player';

interface WebsitePickerProps {
  website: WebsiteName;
  onChange: (website: WebsiteName) => void;
}

const websiteLogo: Record<WebsiteName, string | null> = {
  Sleeper: '/SleeperLogo.png',
  Fleaflicker: '/FleaFlickerLogo.jpg',
  'Manual Roster': null,
};

const buttonStyleFor = (website: WebsiteName) => {
  const isSleeper = website === 'Sleeper';
  const isManual = website === 'Manual Roster';
  return {
    bg: isSleeper ? '#0f2030' : isManual ? '#6b46c1' : '#ffffff',
    color: isSleeper || isManual ? 'white' : 'black',
    _hover: { bg: isSleeper ? '#1a3550' : isManual ? '#553c9a' : '#f0f4f8' },
    _active: { bg: isSleeper ? '#1a3550' : isManual ? '#553c9a' : '#f0f4f8' },
    _focus: { boxShadow: 'none' },
  };
};

const WebsitePicker: React.FC<WebsitePickerProps> = ({ website, onChange }) => {
  const triggerStyle = buttonStyleFor(website);

  return (
    <Menu>
      <MenuButton
        as={Button}
        rightIcon={<ChevronDownIcon />}
        {...triggerStyle}
        border="none"
        px={3}
        py={1}
        borderRadius="md"
      >
        <HStack spacing={2}>
          {websiteLogo[website]
            ? <Image src={`${process.env.PUBLIC_URL}${websiteLogo[website]}`} boxSize="20px" />
            : <Text aria-hidden="true">✎</Text>}
          <Text>{website}</Text>
        </HStack>
      </MenuButton>

      <MenuList minW="unset" w="auto" bg="transparent" boxShadow="none" p={0}>
        <MenuItem
          onClick={() => onChange('Sleeper')}
          bg="#0f2030"
          _hover={{ bg: '#1a3550' }}
          borderRadius="md"
        >
          <HStack spacing={2}>
            <Image src={`${process.env.PUBLIC_URL}${websiteLogo.Sleeper}`} boxSize="20px" />
            <Text color="white">Sleeper</Text>
          </HStack>
        </MenuItem>
        <MenuItem
          onClick={() => onChange('Fleaflicker')}
          bg="#ffffff"
          _hover={{ bg: '#f0f4f8' }}
          borderRadius="md"
        >
          <HStack spacing={2}>
            <Image src={`${process.env.PUBLIC_URL}${websiteLogo.Fleaflicker}`} boxSize="20px" />
            <Text color="black">Fleaflicker</Text>
          </HStack>
        </MenuItem>
        <MenuItem
          onClick={() => onChange('Manual Roster')}
          bg="#6b46c1"
          _hover={{ bg: '#553c9a' }}
          borderRadius="md"
        >
          <HStack spacing={2}>
            <Text color="white" aria-hidden="true">✎</Text>
            <Text color="white">Manual Roster</Text>
          </HStack>
        </MenuItem>
      </MenuList>
    </Menu>
  );
};

export default WebsitePicker;
