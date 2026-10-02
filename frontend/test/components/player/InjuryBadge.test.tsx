import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { InjuryBadge } from '../../../src/components/player/InjuryBadge';

describe('InjuryBadge', () => {
  it('renders nothing without a status', () => {
    // act
    const { container } = render(<InjuryBadge status={null} />);

    // assert
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the short label with the long phrase in the title', () => {
    // act
    render(<InjuryBadge status="Expected to be out until at least Dec 1" />);

    // assert
    const badge = screen.getByTestId('injury-badge');
    expect(badge).toHaveTextContent(/^OUT$/);
    expect(badge).toHaveAttribute('title', 'Expected to be out until at least Dec 1');
    expect(screen.queryByTestId('injury-detail')).not.toBeInTheDocument();
  });

  it('renders the detail as its own line when showDetail is set', () => {
    // act
    render(<InjuryBadge status="Questionable" detail="Left ankle" showDetail />);

    // assert
    expect(screen.getByTestId('injury-badge')).toHaveTextContent('GTD');
    expect(screen.getByTestId('injury-detail')).toHaveTextContent('Left ankle');
  });

  it('omits the detail line and title when there is nothing beyond the label', () => {
    // act
    render(<InjuryBadge status="Probable" showDetail />);

    // assert
    expect(screen.getByTestId('injury-badge')).not.toHaveAttribute('title');
    expect(screen.queryByTestId('injury-detail')).not.toBeInTheDocument();
  });
});
