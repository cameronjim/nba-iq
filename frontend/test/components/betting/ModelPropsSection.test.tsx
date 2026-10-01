import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ModelPropsSection } from '../../../src/components/betting/ModelPropsSection';

describe('ModelPropsSection', () => {
  it('renders nothing until there are model props', () => {
    // arrange + act
    const { container } = render(<ModelPropsSection />);

    // assert
    expect(container).toBeEmptyDOMElement();
  });

  it('lists model props as sentences when given data', () => {
    // arrange + act
    render(<ModelPropsSection lines={[{ id: 'a', sentence: 'Brunson over 26.5 points looks likely.' }]} />);

    // assert
    expect(screen.getByRole('region', { name: 'Model player props' })).toBeInTheDocument();
    expect(screen.getByText('Brunson over 26.5 points looks likely.')).toBeInTheDocument();
  });
});
