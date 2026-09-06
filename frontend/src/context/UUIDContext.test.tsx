import React from 'react';
import { render, screen } from '@testing-library/react';
import { validate as validateUUID } from 'uuid';

import { UUIDProvider, UUID_STORAGE_KEY, useUUID } from './UUIDContext';

const UUIDConsumer = () => <span data-testid="uuid">{useUUID()}</span>;

const renderProvider = () => render(
  <UUIDProvider>
    <UUIDConsumer />
  </UUIDProvider>,
);

beforeEach(() => {
  window.localStorage.clear();
  jest.restoreAllMocks();
});

afterEach(() => {
  jest.restoreAllMocks();
});

it('creates and stores a valid UUID when none exists', () => {
  renderProvider();

  const uuid = screen.getByTestId('uuid').textContent ?? '';
  expect(validateUUID(uuid)).toBe(true);
  expect(window.localStorage.getItem(UUID_STORAGE_KEY)).toBe(uuid);
});

it('reuses the stored UUID after the provider is remounted', () => {
  const firstRender = renderProvider();
  const firstUUID = screen.getByTestId('uuid').textContent;
  firstRender.unmount();

  renderProvider();

  expect(screen.getByTestId('uuid')).toHaveTextContent(firstUUID ?? '');
  expect(window.localStorage.getItem(UUID_STORAGE_KEY)).toBe(firstUUID);
});

it('replaces an invalid stored value with a valid UUID', () => {
  window.localStorage.setItem(UUID_STORAGE_KEY, 'corrupt-value');

  renderProvider();

  const uuid = screen.getByTestId('uuid').textContent ?? '';
  expect(validateUUID(uuid)).toBe(true);
  expect(uuid).not.toBe('corrupt-value');
  expect(window.localStorage.getItem(UUID_STORAGE_KEY)).toBe(uuid);
});

it('provides a valid UUID when localStorage operations throw', () => {
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new DOMException('Storage blocked', 'SecurityError');
  });
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('Storage blocked', 'SecurityError');
  });

  renderProvider();

  expect(validateUUID(screen.getByTestId('uuid').textContent ?? '')).toBe(true);
});