import React from 'react';
import { Button, HStack, Input, VStack } from '@chakra-ui/react';
import WebsitePicker from './WebsitePicker';
import { WebsiteName } from '../types/player';

interface UsernameFormProps {
  website: WebsiteName;
  onWebsiteChange: (w: WebsiteName) => void;
  username: string;
  onUsernameChange: (value: string) => void;
  onSubmit: () => void;
  submitLabel?: string;
}

const placeholderFor = (website: WebsiteName) =>
  website === 'Sleeper' ? 'Enter Sleeper username' : 'Enter your Fleaflicker email';

const UsernameForm: React.FC<UsernameFormProps> = ({
  website,
  onWebsiteChange,
  username,
  onUsernameChange,
  onSubmit,
  submitLabel = 'Load Teams',
}) => (
  <VStack gap={2}>
    <HStack gap={2} justify="center">
      <WebsitePicker website={website} onChange={onWebsiteChange} />
      <Input
        placeholder={placeholderFor(website)}
        value={username}
        onChange={(e) => onUsernameChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onSubmit();
        }}
        maxW="300px"
      />
    </HStack>
    <HStack justify="center">
      <Button onClick={onSubmit} colorScheme="blue" maxWidth="120px">
        {submitLabel}
      </Button>
    </HStack>
  </VStack>
);

export default UsernameForm;
