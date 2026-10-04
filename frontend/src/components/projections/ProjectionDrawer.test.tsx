import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectionDrawer } from './ProjectionDrawer';
import { scenarioData } from '../../test/scenarios';
const props = () => ({
  projection: scenarioData().projections[0],
  expectedSnapshotId: 'test-snapshot-1',
  title: 'Case',
  now: Date.now(),
  timezone: 'America/Detroit',
  mode: 'TEST' as const,
  minute: 0,
  onMinuteChange: () => {},
  onClose: () => {},
});
describe('scenario integrity', () => {
  it('refuses a projection from another snapshot', () => {
    render(
      <ProjectionDrawer {...props()} expectedSnapshotId="different-snapshot" />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('does not match');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
  it('keeps the +15 outcomes fixed and displays unfavorable waiting changes', () => {
    const view = render(<ProjectionDrawer {...props()} minute={0} />);
    const waiting = screen.getByRole('article', {
      name: 'Total waiting at +15 minutes',
    });
    expect(within(waiting).getByText('+13 aircraft-min')).toHaveClass(
      'adverse-change',
    );
    view.rerender(<ProjectionDrawer {...props()} minute={10} />);
    expect(within(waiting).getByText('231')).toBeVisible();
    expect(within(waiting).getByText('218')).toBeVisible();
  });
  it('shows exact model samples while the map timeline interpolates between waypoints', () => {
    render(<ProjectionDrawer {...props()} minute={7.5} />);
    fireEvent.click(screen.getByText('Sampled comparison · +5 min'));
    expect(
      screen.getByRole('table', { name: 'Scenario comparison at +5 minutes' }),
    ).toBeVisible();
    expect(screen.getByRole('cell', { name: '55 aircraft-min' })).toBeVisible();
    expect(screen.getByText(/Exact model samples/)).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });
});
