import React from 'react';
import { Box, HStack, Image, Text } from '@chakra-ui/react';
import { Link as RouterLink, useLocation } from 'react-router-dom';

const links: { to: string; label: string }[] = [
  { to: '/', label: 'My Teams' },
  { to: '/ranks', label: 'Overall Rankings' },
  { to: '/draft-help', label: 'Draft Help' },
  { to: '/wrapped', label: 'League Wrapped' },
];

const NavBar: React.FC = () => {
  const { pathname } = useLocation();

  return (
    <Box
      as="nav"
      bg="#0f2030"
      color="white"
      px={{ base: 3, md: 6 }}
      py={2}
      boxShadow="sm"
      position="sticky"
      top={0}
      zIndex={10}
    >
      <HStack gap={{ base: 3, md: 6 }}>
        <HStack gap={2}>
          <Image src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} boxSize="28px" />
          <Text fontWeight="bold" fontSize={{ base: 'md', md: 'lg' }}>
            Fantasy Football Visualizer
          </Text>
        </HStack>

        <HStack gap={{ base: 2, md: 4 }} ml={{ base: 1, md: 4 }}>
          {links.map((link) => {
            const active = pathname === link.to;
            return (
              <RouterLink key={link.to} to={link.to}>
                <Text
                  fontSize={{ base: 'sm', md: 'md' }}
                  fontWeight={active ? 'bold' : 'normal'}
                  borderBottomWidth={active ? '2px' : '0'}
                  borderColor="blue.300"
                  pb="2px"
                  _hover={{ color: 'blue.200' }}
                >
                  {link.label}
                </Text>
              </RouterLink>
            );
          })}
        </HStack>
      </HStack>
    </Box>
  );
};

export default NavBar;
