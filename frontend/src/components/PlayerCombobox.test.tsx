import React from 'react';
import { ChakraProvider } from '@chakra-ui/react';
import { fireEvent, render, screen } from '@testing-library/react';

import PlayerCombobox from './PlayerCombobox';

const players = Array.from({ length: 15 }, (_, index) => ({
  player_id: String(index + 1),
  name: `Player ${String(index + 1).padStart(2, '0')}`,
  pos: index % 2 ? 'WR' : 'RB',
}));

it('portals a scrollable result list outside a clipped parent', () => {
  jest.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 20,
    y: 40,
    left: 20,
    top: 40,
    right: 320,
    bottom: 72,
    width: 300,
    height: 32,
    toJSON: () => ({}),
  } as DOMRect);

  const { container } = render(
    <ChakraProvider>
      <div style={{ overflow: 'hidden', height: 40 }}>
        <PlayerCombobox players={players} value="" onChange={() => {}} />
      </div>
    </ChakraProvider>,
  );
  fireEvent.focus(screen.getByRole('combobox'));

  const listbox = screen.getByRole('listbox');
  expect(listbox).toBeInTheDocument();
  expect(container.contains(listbox)).toBe(false);
  expect(listbox).toHaveStyle({ position: 'fixed', overflowY: 'auto' });
  expect(screen.getAllByRole('option')).toHaveLength(12);
});