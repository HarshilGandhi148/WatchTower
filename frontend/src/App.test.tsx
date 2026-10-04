import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import App from './App';

describe('unconnected dashboard', () => {
  it('shows unavailable input and exposes no decision controls', () => {
    render(<App />);

    expect(screen.getByRole('status')).toHaveTextContent(
      'No live observations have been received.',
    );
    expect(
      screen.getByRole('heading', { name: 'Connection unavailable' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /approve|edit|ignore/i }),
    ).not.toBeInTheDocument();
  });
});
