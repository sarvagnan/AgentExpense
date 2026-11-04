import {useEffect,useRef,useState,type FocusEvent} from 'react';

/** Apply background snapshots after the pointer and keyboard leave the controls.
 * Explicit actions release immediately, so their own results always appear. */
export function useInteractionSnapshot<T>(value:T,enabled=true){
 const latest=useRef(value);latest.current=value;
 const [held,setHeld]=useState<{value:T}|null>(null),pointer=useRef(false),focus=useRef(false);
 const hold=()=>{if(enabled)setHeld(current=>current||{value:latest.current});};
 const release=()=>{pointer.current=false;focus.current=false;setHeld(null);};
 const interaction={
  onPointerEnter(){pointer.current=true;hold();},
  onPointerLeave(){pointer.current=false;if(!focus.current)setHeld(null);},
  onFocusCapture(){focus.current=true;hold();},
  onBlurCapture(event:FocusEvent<HTMLElement>){if(!event.currentTarget.contains(event.relatedTarget as Node|null)){focus.current=false;if(!pointer.current)setHeld(null);}},
 };
 useEffect(()=>{if(enabled&&(pointer.current||focus.current))hold();},[enabled]);
 return [held?held.value:value,interaction,release] as const;
}
