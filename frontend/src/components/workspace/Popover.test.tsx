import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Popover } from './Popover';

describe('workspace disclosures', () => {
  it('closes with Escape and returns focus to the summary', () => {
    render(
      <Popover summary="Source health">
        <button>Source details</button>
      </Popover>,
    );
    const summary = screen.getByText('Source health');
    fireEvent.click(summary);
    const button = screen.getByRole('button', { name: 'Source details' });
    button.focus();
    fireEvent.keyDown(button, { key: 'Escape' });
    expect(summary.closest('details')).not.toHaveAttribute('open');
    expect(summary).toHaveFocus();
  });
  it('closes outside the disclosure while retaining clicks inside it', () => {
    render(
      <>
        <Popover summary="Weather">
          <button>Weather details</button>
        </Popover>
        <button>Map controls</button>
      </>,
    );
    const summary = screen.getByText('Weather');
    fireEvent.click(summary);
    fireEvent.pointerDown(
      screen.getByRole('button', { name: 'Weather details' }),
    );
    expect(summary.closest('details')).toHaveAttribute('open');
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Map controls' }));
    expect(summary.closest('details')).not.toHaveAttribute('open');
  });
});
