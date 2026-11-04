import {createContext} from 'react';
import type {LiveDrafts} from './use-drafts';

// Inspectors share Dashboard's existing polling, including completed receipt work.
export const LiveDraftsContext=createContext<LiveDrafts|null>(null);
